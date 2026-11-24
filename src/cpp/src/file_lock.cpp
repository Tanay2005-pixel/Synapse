#include "file_lock.hpp"

#include <cerrno>
#include <chrono>
#include <cstring>
#include <stdexcept>
#include <thread>

#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>

namespace synapse {

FileLock::FileLock(const std::filesystem::path& path, Mode mode, int timeout_ms) {
    fd_ = ::open(path.c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0644);
    if (fd_ < 0)
        throw std::runtime_error("cannot open lock file " + path.string() + ": " +
                                 std::strerror(errno));

    const int op = (mode == Mode::Shared ? LOCK_SH : LOCK_EX) | LOCK_NB;
    // Non-blocking attempts with a deadline: a waiting request gets a clear
    // "busy" error instead of hanging forever behind a stuck peer.
    const auto deadline =
        std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    while (::flock(fd_, op) != 0) {
        if (errno != EWOULDBLOCK && errno != EINTR) {
            const std::string why = std::strerror(errno);
            ::close(fd_);
            throw std::runtime_error("flock failed on " + path.string() + ": " + why);
        }
        if (std::chrono::steady_clock::now() >= deadline) {
            ::close(fd_);
            throw LockTimeout("timed out waiting for lock " + path.string());
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
}

FileLock::~FileLock() {
    if (fd_ >= 0) {
        ::flock(fd_, LOCK_UN);
        ::close(fd_);
    }
}

bool valid_lock_key(const std::string& key) {
    if (key.empty() || key.size() > 128)
        return false;
    for (char c : key) {
        bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                  (c >= '0' && c <= '9') || c == '-' || c == '_';
        if (!ok)
            return false;
    }
    return true;
}

}  // namespace synapse
