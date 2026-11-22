#pragma once

// Synapse exclusive file lock manager.
//
// One lock per file id, shared by every process on the machine (every CLI
// invocation, every FastAPI worker).  Design:
//
//   <locks_dir>/<file_id>.guard   never deleted; an exclusive flock(2) on it
//                                 serialises every read-modify-write below.
//                                 The kernel drops the flock if the process
//                                 dies, so a crash can never wedge a file.
//   <locks_dir>/<file_id>.lock    the lock record (owner, session, lease).
//                                 Written to a temp file, fsync'd, then
//                                 rename(2)'d into place, so readers see the
//                                 old record or the new one, never half.
//
// Because every decision ("is it free? -> take it") happens while holding
// the guard flock, there is no check-then-create race.
//
// Lease model (all times are UNIX seconds from the same clock):
//   active     now <  lease_expires                  -> held, owner is working
//   idle       lease_expires <= now < last_heartbeat + abandon_after
//                                                    -> still held; never
//                                                       taken from the owner
//   abandoned  now >= last_heartbeat + abandon_after -> another user's
//                                                       acquire may reclaim it
// The owner can always heartbeat/resume while the record is still theirs.

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace synapse {

namespace fs = std::filesystem;

struct LockRecord {
    std::int64_t file_id = 0;
    std::int64_t user_id = 0;
    std::string session_id;
    std::int64_t acquired_at = 0;
    std::int64_t lease_expires = 0;
    std::int64_t last_heartbeat = 0;
    std::int64_t abandon_after = 0;  // seconds after last_heartbeat
};

enum class LockState { Active, Idle, Abandoned };

enum class LockResult {
    Granted,      // lock was free; caller now owns it
    Renewed,      // caller already owned it with this session; lease extended
    Reclaimed,    // previous owner abandoned it; caller now owns it
    HeldByOther,  // someone else (or the same user in another session) holds it
    NotOwner,     // release/heartbeat by someone who does not hold the lock
    NotLocked,    // release/heartbeat but there is no lock (or it was reclaimed)
    Released      // caller's lock was removed
};

struct LockOutcome {
    LockResult result;
    std::optional<LockRecord> record;    // holder after the operation (if any)
    std::optional<LockRecord> previous;  // the abandoned record, on Reclaimed
};

class LockManager {
public:
    explicit LockManager(fs::path locks_dir, int guard_timeout_ms = 5000);

    LockOutcome acquire(std::int64_t file_id, std::int64_t user_id,
                        const std::string& session_id,
                        std::int64_t lease_seconds, std::int64_t abandon_seconds);

    LockOutcome heartbeat(std::int64_t file_id, std::int64_t user_id,
                          const std::string& session_id, std::int64_t lease_seconds);

    LockOutcome release(std::int64_t file_id, std::int64_t user_id,
                        const std::string& session_id);

    std::optional<LockRecord> status(std::int64_t file_id);

    // Every current lock record (one guarded read per file). Records that
    // cannot be parsed are returned as file ids in `corrupt` so callers can
    // treat those files as locked.
    std::vector<LockRecord> list_all(std::vector<std::int64_t>& corrupt);

    static LockState state_of(const LockRecord& record, std::int64_t now);
    static std::int64_t now();
    static bool valid_session_id(const std::string& session_id);

private:
    class Guard;  // RAII exclusive flock on <file_id>.guard

    fs::path record_path(std::int64_t file_id) const;
    fs::path guard_path(std::int64_t file_id) const;
    std::optional<LockRecord> read_record(std::int64_t file_id) const;
    void write_record(const LockRecord& record) const;
    void remove_record(std::int64_t file_id) const;

    fs::path dir_;
    int guard_timeout_ms_;
};

const char* to_string(LockState state);
const char* to_string(LockResult result);

}  // namespace synapse
