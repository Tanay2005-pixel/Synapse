#pragma once

// Cross-process reader/writer lock on one file, built on flock(2).
//
// Why flock and not std::mutex: the Python backend starts a *new process*
// for every engine/lock call. A std::mutex lives in one process's memory,
// so two processes would each have their own mutex and never see each
// other. flock is kept by the kernel on the lock file itself, so every
// process that opens the same path competes for the same lock.
//
//   Shared     many holders at once      (downloads / reads)
//   Exclusive  one holder, no readers    (uploads / modifications)
//
// The kernel drops the lock when the file descriptor is closed, which also
// happens automatically if the process crashes or is killed, so a dead
// process can never leave a file locked forever.

#include <filesystem>
#include <stdexcept>
#include <string>

namespace synapse {

class FileLock {
public:
    enum class Mode { Shared, Exclusive };

    // Blocks (polling) until the lock is granted or timeout_ms passes.
    // Throws LockTimeout on timeout, std::runtime_error on other failures.
    FileLock(const std::filesystem::path& path, Mode mode, int timeout_ms);
    ~FileLock();

    FileLock(const FileLock&) = delete;
    FileLock& operator=(const FileLock&) = delete;

private:
    int fd_ = -1;
};

struct LockTimeout : std::runtime_error {
    using std::runtime_error::runtime_error;
};

// Lock keys become file names, so only [A-Za-z0-9_-], 1..128 chars.
bool valid_lock_key(const std::string& key);

}  // namespace synapse
