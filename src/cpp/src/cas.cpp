#include "cas.hpp"
#include "sha256.hpp"

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <fstream>
#include <random>
#include <stdexcept>

#include <fcntl.h>
#include <unistd.h>

// Concurrency model (many engine processes may share one storage dir):
//
//  * A chunk's file name IS its SHA-256, so two processes storing the same
//    name are storing the same bytes. Whoever finishes first wins; the
//    other simply finds it already there.
//  * Chunks are never written in place. Each writer writes its own uniquely
//    named temp file, fsyncs it, and then publishes it with link(2).
//    link() is atomic and FAILS with EEXIST if the target exists, so an
//    existing chunk is never overwritten and readers can never see a
//    half-written chunk under its final name.
//  * Reads re-hash the bytes and refuse a chunk whose SHA-256 does not match.

namespace {

std::runtime_error sys_error(const std::string& what) {
    return std::runtime_error(what + ": " + std::strerror(errno));
}

// Unique across processes (pid), threads/calls in one process (counter),
// and pid reuse (random). Uniqueness is also enforced by O_EXCL below.
std::string unique_suffix() {
    static std::atomic<unsigned long> counter{0};
    static thread_local std::mt19937_64 rng{std::random_device{}()};
    return std::to_string(::getpid()) + "_" + std::to_string(counter++) + "_" +
           std::to_string(rng());
}

void write_all(int fd, const uint8_t* data, size_t len, const fs::path& path) {
    while (len > 0) {
        ssize_t n = ::write(fd, data, len);
        if (n < 0) {
            if (errno == EINTR) continue;
            throw sys_error("write failed on " + path.string());
        }
        data += n;
        len -= static_cast<size_t>(n);
    }
}

// Deletes a temp file when it goes out of scope (also on exceptions).
struct TempFileCleanup {
    fs::path path;
    ~TempFileCleanup() {
        if (!path.empty()) ::unlink(path.c_str());
    }
};

void ensure_directory(const fs::path& dir) {
    std::error_code ec;
    fs::create_directories(dir, ec);
    // Another process may have created it at the same moment; that's fine.
    if (!fs::is_directory(dir))
        throw std::runtime_error("cannot create directory " + dir.string() +
                                 (ec ? ": " + ec.message() : ""));
}

}  // namespace

CASStorage::CASStorage(const fs::path& base_dir) : root_dir(base_dir)
{
    tmp_dir = root_dir / "tmp";
    ensure_directory(tmp_dir);

    // Sweep temp files left behind by an engine process that was killed
    // mid-write. Only old files are removed: every live write finishes in
    // far less than an hour, so this can never delete a file in use.
    std::error_code ec;
    const auto cutoff = fs::file_time_type::clock::now() - std::chrono::hours(1);
    for (fs::directory_iterator it(tmp_dir, ec), end; !ec && it != end; it.increment(ec)) {
        std::error_code e2;
        const std::string name = it->path().filename().string();
        if (name.size() > 4 && name.compare(name.size() - 4, 4, ".tmp") == 0 &&
            it->is_regular_file(e2) && it->last_write_time(e2) < cutoff && !e2)
            fs::remove(it->path(), e2);
    }
}

fs::path CASStorage::get_chunk_path(const std::string& hash) const {
    if (hash.length() < 2)
        throw std::invalid_argument("hash length must be at least 2");
    std::string prefix = hash.substr(0, 2);
    return root_dir / prefix / hash;
}

bool CASStorage::chunk_exists(const std::string& hash) const {
    return fs::exists(get_chunk_path(hash));
}

// Returns true if this call stored the chunk, false if it already existed
// (stored earlier, or by a concurrent process). Throws on real failures, so
// a manifest can never point at a chunk that was not stored.
bool CASStorage::store_chunk(const std::string& hash, const std::vector<uint8_t>& data) {
    fs::path target_path = get_chunk_path(hash);

    if (fs::exists(target_path))
        return false;

    ensure_directory(target_path.parent_path());

    // 1. Write to a private temp file with a unique name.
    TempFileCleanup temp;
    int fd = -1;
    for (int attempt = 0; attempt < 10 && fd < 0; ++attempt) {
        temp.path = tmp_dir / (hash + "_" + unique_suffix() + ".tmp");
        fd = ::open(temp.path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
        if (fd < 0 && errno != EEXIST)
            throw sys_error("temp chunk file can't be opened " + temp.path.string());
    }
    if (fd < 0)
        throw std::runtime_error("could not create a unique temp chunk file");

    try {
        write_all(fd, data.data(), data.size(), temp.path);
        if (::fsync(fd) != 0)  // bytes are on disk before the chunk becomes visible
            throw sys_error("fsync failed on " + temp.path.string());
    } catch (...) {
        ::close(fd);
        throw;
    }
    if (::close(fd) != 0)
        throw sys_error("close failed on " + temp.path.string());

    // 2. Publish atomically. link() never replaces an existing file.
    if (::link(temp.path.c_str(), target_path.c_str()) == 0)
        return true;  // temp is unlinked by TempFileCleanup; chunk keeps its link
    if (errno == EEXIST)
        return false;  // a concurrent writer stored the identical chunk first
    throw sys_error("cannot publish chunk " + hash);
}

std::vector<uint8_t> CASStorage::read_chunk(const std::string& hash) {
    fs::path target = get_chunk_path(hash);

    std::ifstream in(target, std::ios::binary);
    if (!in)
        throw std::runtime_error("Chunk failure " + hash);

    in.seekg(0, std::ios::end);
    const std::streamoff end = in.tellg();
    if (end < 0)
        throw std::runtime_error("cannot determine size of chunk " + hash);
    in.seekg(0, std::ios::beg);

    std::vector<uint8_t> buffer(static_cast<size_t>(end));
    in.read(reinterpret_cast<char*>(buffer.data()), static_cast<std::streamsize>(buffer.size()));
    if (in.gcount() != static_cast<std::streamsize>(buffer.size()))
        throw std::runtime_error("short read on chunk " + hash);

    // Integrity check: the content must still hash to its name.
    if (compute_sha256(buffer) != hash)
        throw std::runtime_error("chunk " + hash + " is corrupted (SHA-256 mismatch)");

    return buffer;
}
