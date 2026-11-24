// synapse_lock: command-line front end for synapse::LockManager.
//
// Every invocation is a separate process; correctness across invocations
// comes from the flock-protected lock records (see lock_manager.hpp).
//
// Output: one JSON object on stdout (errors: JSON on stderr).
// Exit codes:
//   0  success (granted / renewed / reclaimed / released / status / list)
//   1  usage or internal error
//   2  held by another user or session        (HTTP 409)
//   3  caller does not own the lock            (HTTP 403)
//   4  no lock exists (or it was reclaimed)    (HTTP 409/410 at the API)
//   5  hold: lock is busy, timed out waiting  (HTTP 503 at the API)
//
// `hold` is different from the other commands: it keeps running. It takes a
// shared or exclusive flock on <locks_dir>/<key>.io, prints one JSON line
// ("locked"), and then waits until its stdin is closed. The Python request
// keeps that pipe open for the whole upload/download and closes it when it
// is done; if the Python process dies, the OS closes the pipe for it. When
// this process exits (normally, killed, or crashed) the kernel releases the
// flock, so the lock can never outlive the request that took it.

#include "file_lock.hpp"
#include "lock_manager.hpp"

#include <cerrno>
#include <cstdint>
#include <iostream>
#include <unistd.h>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using synapse::LockManager;
using synapse::LockOutcome;
using synapse::LockRecord;
using synapse::LockResult;

namespace {

constexpr int EXIT_OK = 0;
constexpr int EXIT_ERROR = 1;
constexpr int EXIT_HELD = 2;
constexpr int EXIT_NOT_OWNER = 3;
constexpr int EXIT_NOT_LOCKED = 4;
constexpr int EXIT_BUSY = 5;

void usage(const char* bin) {
    std::cerr
        << "Usage:\n"
        << "  " << bin << " hold      <locks_dir> <key> <shared|exclusive> <timeout_ms>\n"
        << "  " << bin << " acquire   <locks_dir> <file_id> <user_id> <session_id> <lease_s> <abandon_s>\n"
        << "  " << bin << " heartbeat <locks_dir> <file_id> <user_id> <session_id> <lease_s>\n"
        << "  " << bin << " release   <locks_dir> <file_id> <user_id> <session_id>\n"
        << "  " << bin << " status    <locks_dir> <file_id>\n"
        << "  " << bin << " list      <locks_dir>\n";
}

std::int64_t parse_int(const std::string& text, const char* name) {
    if (text.empty() || text.size() > 18)
        throw std::invalid_argument(std::string("invalid ") + name);
    for (char c : text)
        if (c < '0' || c > '9')
            throw std::invalid_argument(std::string("invalid ") + name);
    return std::stoll(text);
}

// Only digits and [A-Za-z0-9_-] ever reach the output, so no escaping needed
// for record fields; error messages are escaped.
std::string escape_json(const std::string& in) {
    std::ostringstream ss;
    for (char c : in) {
        switch (c) {
            case '"': ss << "\\\""; break;
            case '\\': ss << "\\\\"; break;
            case '\n': ss << "\\n"; break;
            case '\r': ss << "\\r"; break;
            case '\t': ss << "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) ss << ' ';
                else ss << c;
        }
    }
    return ss.str();
}

std::string record_json(const LockRecord& r, std::int64_t now) {
    std::ostringstream ss;
    ss << "{\"file_id\": " << r.file_id
       << ", \"user_id\": " << r.user_id
       << ", \"session_id\": \"" << r.session_id << "\""
       << ", \"acquired_at\": " << r.acquired_at
       << ", \"lease_expires\": " << r.lease_expires
       << ", \"last_heartbeat\": " << r.last_heartbeat
       << ", \"abandon_after\": " << r.abandon_after
       << ", \"state\": \"" << synapse::to_string(LockManager::state_of(r, now)) << "\"}";
    return ss.str();
}

int print_outcome(const LockOutcome& o) {
    const std::int64_t now = LockManager::now();
    std::cout << "{\"status\": \"" << synapse::to_string(o.result) << "\""
              << ", \"now\": " << now
              << ", \"lock\": " << (o.record ? record_json(*o.record, now) : "null");
    if (o.previous)
        std::cout << ", \"previous\": " << record_json(*o.previous, now);
    std::cout << "}\n";

    switch (o.result) {
        case LockResult::Granted:
        case LockResult::Renewed:
        case LockResult::Reclaimed:
        case LockResult::Released:
            return EXIT_OK;
        case LockResult::HeldByOther:
            return EXIT_HELD;
        case LockResult::NotOwner:
            return EXIT_NOT_OWNER;
        case LockResult::NotLocked:
            return EXIT_NOT_LOCKED;
    }
    return EXIT_ERROR;
}

int hold(const std::string& locks_dir, const std::string& key,
         const std::string& mode_text, std::int64_t timeout_ms) {
    if (!synapse::valid_lock_key(key))
        throw std::invalid_argument("invalid lock key");
    if (mode_text != "shared" && mode_text != "exclusive")
        throw std::invalid_argument("mode must be shared or exclusive");
    if (timeout_ms > 24LL * 3600 * 1000)
        throw std::invalid_argument("timeout too large");
    const auto mode = mode_text == "shared" ? synapse::FileLock::Mode::Shared
                                            : synapse::FileLock::Mode::Exclusive;

    std::filesystem::create_directories(locks_dir);
    const std::filesystem::path path = std::filesystem::path(locks_dir) / (key + ".io");

    try {
        synapse::FileLock lock(path, mode, static_cast<int>(timeout_ms));
        std::cout << "{\"status\": \"locked\", \"key\": \"" << key
                  << "\", \"mode\": \"" << mode_text << "\", \"pid\": " << ::getpid()
                  << "}" << std::endl;  // endl flushes: the caller is waiting for this line

        // Hold until the caller closes our stdin (EOF) or the pipe breaks.
        char buf[256];
        for (;;) {
            ssize_t n = ::read(STDIN_FILENO, buf, sizeof buf);
            if (n == 0) break;                         // EOF: caller is done
            if (n < 0 && errno != EINTR) break;        // pipe error: caller is gone
        }
    } catch (const synapse::LockTimeout& e) {
        std::cout << "{\"status\": \"busy\", \"key\": \"" << key << "\"}" << std::endl;
        return EXIT_BUSY;
    }
    return EXIT_OK;  // FileLock destructor released the flock
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc < 3) {
        usage(argv[0]);
        return EXIT_ERROR;
    }

    const std::string command = argv[1];
    try {
        if (command == "hold" && argc == 6)
            return hold(argv[2], argv[3], argv[4], parse_int(argv[5], "timeout_ms"));

        LockManager manager(argv[2]);

        if (command == "list" && argc == 3) {
            const std::int64_t now = LockManager::now();
            std::vector<std::int64_t> corrupt;
            std::vector<LockRecord> records = manager.list_all(corrupt);
            std::cout << "{\"status\": \"ok\", \"now\": " << now << ", \"locks\": [";
            for (size_t i = 0; i < records.size(); ++i)
                std::cout << (i ? ", " : "") << record_json(records[i], now);
            std::cout << "], \"corrupt\": [";
            for (size_t i = 0; i < corrupt.size(); ++i)
                std::cout << (i ? ", " : "") << corrupt[i];
            std::cout << "]}\n";
            return EXIT_OK;
        }

        const std::int64_t file_id = parse_int(argv[3], "file_id");

        if (command == "acquire" && argc == 8) {
            return print_outcome(manager.acquire(
                file_id, parse_int(argv[4], "user_id"), argv[5],
                parse_int(argv[6], "lease_s"), parse_int(argv[7], "abandon_s")));
        }
        if (command == "heartbeat" && argc == 7) {
            return print_outcome(manager.heartbeat(
                file_id, parse_int(argv[4], "user_id"), argv[5],
                parse_int(argv[6], "lease_s")));
        }
        if (command == "release" && argc == 6) {
            return print_outcome(manager.release(
                file_id, parse_int(argv[4], "user_id"), argv[5]));
        }
        if (command == "status" && argc == 4) {
            const std::int64_t now = LockManager::now();
            std::optional<LockRecord> r = manager.status(file_id);
            std::cout << "{\"status\": \"" << (r ? "locked" : "unlocked") << "\""
                      << ", \"now\": " << now
                      << ", \"lock\": " << (r ? record_json(*r, now) : "null") << "}\n";
            return EXIT_OK;
        }

        usage(argv[0]);
        return EXIT_ERROR;
    } catch (const std::exception& e) {
        std::cerr << "{\"status\": \"error\", \"message\": \"" << escape_json(e.what()) << "\"}\n";
        return EXIT_ERROR;
    }
}
