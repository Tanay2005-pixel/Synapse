#include "chunker.hpp"
#include "sha256.hpp"

#include <fstream>
#include <stdexcept>

FileEngine::FileEngine(CASStorage& storage) : cas(storage) {}

FileManifest FileEngine::split_file(const fs::path& filepath, size_t chunk_size) {
    if (chunk_size == 0)
        throw std::invalid_argument("Invalid size");

    std::ifstream in(filepath, std::ios::binary);
    if (!in)
        throw std::runtime_error("ailed to open file: " + filepath.string());

    in.seekg(0, std::ios::end);
    size_t total_size = static_cast<size_t>(in.tellg());
    in.seekg(0, std::ios::beg);

    FileManifest man;
    man.original_filename = filepath.filename().string();
    man.total_size = total_size;
    man.chunk_size = chunk_size;

    std::vector<uint8_t> buffer(chunk_size);
    int index = 0;

    while (in) {
        in.read(reinterpret_cast<char*>(buffer.data()), chunk_size);
        std::streamsize bytes_read = in.gcount();

        if (bytes_read <= 0)break;

        std::vector<uint8_t> chunk_data(buffer.begin(), buffer.begin() + bytes_read);

        std::string hash = compute_sha256(chunk_data);

        cas.store_chunk(hash, chunk_data);

        man.chunks.push_back({index, hash, static_cast<size_t>(bytes_read)});
        index++;
    }
    return man;
}

void FileEngine::assemble_file(const std::vector<std::string>& chunk_hashes, const fs::path& dest_path) {
    if (dest_path.has_parent_path())
        fs::create_directories(dest_path.parent_path());

    std::ofstream out(dest_path, std::ios::binary | std::ios::trunc);
    if (!out)
        throw std::runtime_error("failed to open destination: " + dest_path.string());

    for (const auto& hash : chunk_hashes) {
        std::vector<uint8_t> chunk = cas.read_chunk(hash);

        out.write(reinterpret_cast<const char*>(chunk.data()), chunk.size());

        if (!out) 
            throw std::runtime_error("Failed while writing chunk data to: " + dest_path.string());
        
    }
}
