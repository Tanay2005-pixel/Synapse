#include "lock_manager.hpp"

#include <cerrno>
#include <chrono>
#include <cstring>
#include <ctime>
#include <fstream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <thread>

#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>

namespace synapse {

namespace {

std::runtime_error sys_error(const std::string& what) {
    return std::runtime_error(what + ": " + std::strerror(errno));
}

void require_id(std::int64_t id, const char* name) {
    if (id <= 0)
        throw std::invalid_argument(std::string(name) + " must be a positive integer");
}

void require_positive(std::int64_t seconds, const char* name) {
    if (seconds <= 0)
        throw std::invalid_argument(std::string(name) + " must be > 0 seconds");
}

}  // namespace

// ---------------------------------------------------------------------------
// Guard: exclusive flock on <file_id>.guard for the lifetime of the object.
// ---------------------------------------------------------------------------
class LockManager::Guard {
public:
    Guard(const fs::path& path, int timeout_ms) {
        fd_ = ::open(path.c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0644);
        if (fd_ < 0)
            throw sys_error("cannot open guard " + path.string());

        // Non-blocking attempts with a deadline, so a wedged peer can never
        // hang a request forever. Critical sections are microseconds long.
        const auto deadline =
            std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
        while (::flock(fd_, LOCK_EX | LOCK_NB) != 0) {
            if (errno != EWOULDBLOCK && errno != EINTR) {
                int saved = errno;
                ::close(fd_);
                errno = saved;
                throw sys_error("flock failed on " + path.string());
            }
            if (std::chrono::steady_clock::now() >= deadline) {
                ::close(fd_);
                throw std::runtime_error("timed out waiting for lock guard " + path.string());
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
    }

    ~Guard() {
        if (fd_ >= 0) {
            ::flock(fd_, LOCK_UN);
            ::close(fd_);
        }
    }

    Guard(const Guard&) = delete;
    Guard& operator=(const Guard&) = delete;

private:
    int fd_ = -1;
};

// ---------------------------------------------------------------------------
// LockManager
// ---------------------------------------------------------------------------
LockManager::LockManager(fs::path locks_dir, int guard_timeout_ms)
    : dir_(std::move(locks_dir)), guard_timeout_ms_(guard_timeout_ms) {
    fs::create_directories(dir_);
}

std::int64_t LockManager::now() {
    return static_cast<std::int64_t>(std::time(nullptr));
}

bool LockManager::valid_session_id(const std::string& s) {
    if (s.size() < 8 || s.size() > 128)
        return false;
    for (char c : s) {
        bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                  (c >= '0' && c <= '9') || c == '-' || c == '_';
        if (!ok)
            return false;
    }
    return true;
}

LockState LockManager::state_of(const LockRecord& r, std::int64_t t) {
    if (t < r.lease_expires)
        return LockState::Active;
    if (t < r.last_heartbeat + r.abandon_after)
        return LockState::Idle;
    return LockState::Abandoned;
}

fs::path LockManager::record_path(std::int64_t file_id) const {
    return dir_ / (std::to_string(file_id) + ".lock");
}

fs::path LockManager::guard_path(std::int64_t file_id) const {
    return dir_ / (std::to_string(file_id) + ".guard");
}

std::optional<LockRecord> LockManager::read_record(std::int64_t file_id) const {
    const fs::path path = record_path(file_id);
    std::ifstream in(path);
    if (!in) {
        if (!fs::exists(path))
            return std::nullopt;
        throw std::runtime_error("cannot read lock record " + path.string());
    }

    std::map<std::string, std::string> kv;
    std::string line;
    while (std::getline(in, line)) {
        auto eq = line.find('=');
        if (eq != std::string::npos)
            kv[line.substr(0, eq)] = line.substr(eq + 1);
    }

    // Fail closed: an unreadable record must never be treated as "free".
    auto num = [&](const char* key) -> std::int64_t {
        auto it = kv.find(key);
        if (it == kv.end())
            throw std::runtime_error("corrupt lock record " + path.string() + " (missing " + key + ")");
        try {
            size_t used = 0;
            long long v = std::stoll(it->second, &used);
            if (used != it->second.size())
                throw std::invalid_argument(key);
            return v;
        } catch (const std::exception&) {
            throw std::runtime_error("corrupt lock record " + path.string() + " (bad " + key + ")");
        }
    };

    LockRecord r;
    r.file_id = num("file_id");
    r.user_id = num("user_id");
    r.acquired_at = num("acquired_at");
    r.lease_expires = num("lease_expires");
    r.last_heartbeat = num("last_heartbeat");
    r.abandon_after = num("abandon_after");
    auto sid = kv.find("session_id");
    if (sid == kv.end() || !valid_session_id(sid->second) || r.file_id != file_id)
        throw std::runtime_error("corrupt lock record " + path.string());
    r.session_id = sid->second;
    return r;
}

void LockManager::write_record(const LockRecord& r) const {
    const fs::path final_path = record_path(r.file_id);
    const fs::path tmp_path =
        dir_ / (std::to_string(r.file_id) + ".lock.tmp." + std::to_string(::getpid()));

    std::ostringstream body;
    body << "file_id=" << r.file_id << "\n"
         << "user_id=" << r.user_id << "\n"
         << "session_id=" << r.session_id << "\n"
         << "acquired_at=" << r.acquired_at << "\n"
         << "lease_expires=" << r.lease_expires << "\n"
         << "last_heartbeat=" << r.last_heartbeat << "\n"
         << "abandon_after=" << r.abandon_after << "\n";
    const std::string data = body.str();

    int fd = ::open(tmp_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0)
        throw sys_error("cannot create " + tmp_path.string());

    const char* p = data.data();
    size_t left = data.size();
    while (left > 0) {
        ssize_t n = ::write(fd, p, left);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            int saved = errno;
            ::close(fd);
            ::unlink(tmp_path.c_str());
            errno = saved;
            throw sys_error("cannot write " + tmp_path.string());
        }
        p += n;
        left -= static_cast<size_t>(n);
    }
    if (::fsync(fd) != 0) {
        int saved = errno;
        ::close(fd);
        ::unlink(tmp_path.c_str());
        errno = saved;
        throw sys_error("fsync failed on " + tmp_path.string());
    }
    ::close(fd);

    if (::rename(tmp_path.c_str(), final_path.c_str()) != 0) {
        int saved = errno;
        ::unlink(tmp_path.c_str());
        errno = saved;
        throw sys_error("cannot rename lock record into place");
    }
}

void LockManager::remove_record(std::int64_t file_id) const {
    if (::unlink(record_path(file_id).c_str()) != 0 && errno != ENOENT)
        throw sys_error("cannot remove lock record");
}

LockOutcome LockManager::acquire(std::int64_t file_id, std::int64_t user_id,
                                 const std::string& session_id,
                                 std::int64_t lease_seconds, std::int64_t abandon_seconds) {
    require_id(file_id, "file_id");
    require_id(user_id, "user_id");
    require_positive(lease_seconds, "lease");
    require_positive(abandon_seconds, "abandon");
    if (abandon_seconds < lease_seconds)
        throw std::invalid_argument("abandon must be >= lease");
    if (!valid_session_id(session_id))
        throw std::invalid_argument("invalid session_id");

    Guard guard(guard_path(file_id), guard_timeout_ms_);
    const std::int64_t t = now();
    std::optional<LockRecord> current = read_record(file_id);

    if (current) {
        if (current->user_id == user_id && current->session_id == session_id) {
            current->last_heartbeat = t;
            current->lease_expires = t + lease_seconds;
            write_record(*current);
            return {LockResult::Renewed, current, std::nullopt};
        }
        if (state_of(*current, t) != LockState::Abandoned)
            return {LockResult::HeldByOther, current, std::nullopt};
    }

    LockRecord fresh;
    fresh.file_id = file_id;
    fresh.user_id = user_id;
    fresh.session_id = session_id;
    fresh.acquired_at = t;
    fresh.last_heartbeat = t;
    fresh.lease_expires = t + lease_seconds;
    fresh.abandon_after = abandon_seconds;
    write_record(fresh);

    if (current)
        return {LockResult::Reclaimed, fresh, current};
    return {LockResult::Granted, fresh, std::nullopt};
}

LockOutcome LockManager::heartbeat(std::int64_t file_id, std::int64_t user_id,
                                   const std::string& session_id, std::int64_t lease_seconds) {
    require_id(file_id, "file_id");
    require_id(user_id, "user_id");
    require_positive(lease_seconds, "lease");
    if (!valid_session_id(session_id))
        throw std::invalid_argument("invalid session_id");

    Guard guard(guard_path(file_id), guard_timeout_ms_);
    std::optional<LockRecord> current = read_record(file_id);
    if (!current)
        return {LockResult::NotLocked, std::nullopt, std::nullopt};
    if (current->user_id != user_id || current->session_id != session_id)
        return {LockResult::NotOwner, current, std::nullopt};

    const std::int64_t t = now();
    current->last_heartbeat = t;
    current->lease_expires = t + lease_seconds;
    write_record(*current);
    return {LockResult::Renewed, current, std::nullopt};
}

LockOutcome LockManager::release(std::int64_t file_id, std::int64_t user_id,
                                 const std::string& session_id) {
    require_id(file_id, "file_id");
    require_id(user_id, "user_id");
    if (!valid_session_id(session_id))
        throw std::invalid_argument("invalid session_id");

    Guard guard(guard_path(file_id), guard_timeout_ms_);
    std::optional<LockRecord> current = read_record(file_id);
    if (!current)
        return {LockResult::NotLocked, std::nullopt, std::nullopt};
    if (current->user_id != user_id || current->session_id != session_id)
        return {LockResult::NotOwner, current, std::nullopt};

    remove_record(file_id);
    return {LockResult::Released, std::nullopt, current};
}

std::optional<LockRecord> LockManager::status(std::int64_t file_id) {
    require_id(file_id, "file_id");
    // Take the guard so we never observe a record mid-decision.
    Guard guard(guard_path(file_id), guard_timeout_ms_);
    return read_record(file_id);
}

std::vector<LockRecord> LockManager::list_all(std::vector<std::int64_t>& corrupt) {
    std::vector<LockRecord> records;
    for (const auto& entry : fs::directory_iterator(dir_)) {
        if (!entry.is_regular_file())
            continue;
        const std::string name = entry.path().filename().string();
        // Only "<digits>.lock" (skips guards and temp files).
        const std::string suffix = ".lock";
        if (name.size() <= suffix.size() ||
            name.compare(name.size() - suffix.size(), suffix.size(), suffix) != 0)
            continue;
        const std::string digits = name.substr(0, name.size() - suffix.size());
        if (digits.empty() || digits.size() > 18 ||
            digits.find_first_not_of("0123456789") != std::string::npos)
            continue;

        const std::int64_t file_id = std::stoll(digits);
        if (file_id <= 0)
            continue;
        try {
            std::optional<LockRecord> r = status(file_id);
            if (r)
                records.push_back(*r);
        } catch (const std::runtime_error&) {
            corrupt.push_back(file_id);
        }
    }
    return records;
}

const char* to_string(LockState s) {
    switch (s) {
        case LockState::Active: return "active";
        case LockState::Idle: return "idle";
        case LockState::Abandoned: return "abandoned";
    }
    return "unknown";
}

const char* to_string(LockResult r) {
    switch (r) {
        case LockResult::Granted: return "granted";
        case LockResult::Renewed: return "renewed";
        case LockResult::Reclaimed: return "reclaimed";
        case LockResult::HeldByOther: return "held_by_other";
        case LockResult::NotOwner: return "not_owner";
        case LockResult::NotLocked: return "not_locked";
        case LockResult::Released: return "released";
    }
    return "unknown";
}

}  // namespace synapse
