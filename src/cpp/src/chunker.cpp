#include "chunker.hpp"
#include "sha256.hpp"

#include <atomic>
#include <cerrno>
#include <cstring>
#include <fstream>
#include <random>
#include <stdexcept>

#include <fcntl.h>
#include <unistd.h>

FileEngine::FileEngine(CASStorage& storage) : cas(storage) {}

FileManifest FileEngine::split_file(const fs::path& filepath, size_t chunk_size) {
    if (chunk_size == 0)
        throw std::invalid_argument("Invalid size");

    std::ifstream in(filepath, std::ios::binary);
    if (!in)
        throw std::runtime_error("failed to open file: " + filepath.string());

    in.seekg(0, std::ios::end);
    size_t total_size = static_cast<size_t>(in.tellg());
    in.seekg(0, std::ios::beg);

    FileManifest man;
    man.original_filename = filepath.filename().string();
    man.total_size = total_size;
    man.chunk_size = chunk_size;

    std::vector<uint8_t> buffer(chunk_size);
    int index = 0;
    size_t bytes_seen = 0;

    while (in) {
        in.read(reinterpret_cast<char*>(buffer.data()), chunk_size);
        std::streamsize bytes_read = in.gcount();

        if (bytes_read <= 0)break;

        std::vector<uint8_t> chunk_data(buffer.begin(), buffer.begin() + bytes_read);

        std::string hash = compute_sha256(chunk_data);

        // Throws on a real storage failure; "already exists" is fine (dedup).
        cas.store_chunk(hash, chunk_data);

        man.chunks.push_back({index, hash, static_cast<size_t>(bytes_read)});
        bytes_seen += static_cast<size_t>(bytes_read);
        index++;
    }
    // A read error mid-file must not produce a short "successful" manifest.
    if (in.bad() || bytes_seen != total_size)
        throw std::runtime_error("read error while splitting " + filepath.string());
    return man;
}

// The output is first built in a uniquely named temp file next to dest_path,
// and only renamed to dest_path once every chunk was read, verified and
// written. rename(2) is atomic, so dest_path is either the complete file or
// untouched: a failed download never leaves a half-written file behind
// under the real name.
void FileEngine::assemble_file(const std::vector<std::string>& chunk_hashes, const fs::path& dest_path) {
    const fs::path dir = dest_path.has_parent_path() ? dest_path.parent_path() : fs::path(".");
    {
        std::error_code ec;
        fs::create_directories(dir, ec);
    }

    static std::atomic<unsigned long> counter{0};
    std::random_device rd;
    fs::path part;
    int fd = -1;
    for (int attempt = 0; attempt < 10 && fd < 0; ++attempt) {
        part = dir / ("." + dest_path.filename().string() + ".part_" +
                      std::to_string(::getpid()) + "_" + std::to_string(counter++) + "_" +
                      std::to_string(rd()));
        fd = ::open(part.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
        if (fd < 0 && errno != EEXIST)
            throw std::runtime_error("failed to open destination: " + part.string() + ": " +
                                     std::strerror(errno));
    }
    if (fd < 0)
        throw std::runtime_error("could not create a unique temp output file");

    try {
        for (const auto& hash : chunk_hashes) {
            std::vector<uint8_t> chunk = cas.read_chunk(hash);  // SHA-256 verified

            const uint8_t* p = chunk.data();
            size_t left = chunk.size();
            while (left > 0) {
                ssize_t n = ::write(fd, p, left);
                if (n < 0) {
                    if (errno == EINTR) continue;
                    throw std::runtime_error("Failed while writing chunk data to: " +
                                             dest_path.string() + ": " + std::strerror(errno));
                }
                p += n;
                left -= static_cast<size_t>(n);
            }
        }
        if (::fsync(fd) != 0)
            throw std::runtime_error("fsync failed on " + part.string());
        const int rc = ::close(fd);
        fd = -1;
        if (rc != 0)
            throw std::runtime_error("close failed on " + part.string());
        if (::rename(part.c_str(), dest_path.c_str()) != 0)
            throw std::runtime_error("cannot move output into place: " +
                                     std::string(std::strerror(errno)));
    } catch (...) {
        if (fd >= 0) ::close(fd);
        ::unlink(part.c_str());
        throw;
    }
}
