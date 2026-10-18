#pragma once
#include "cas.hpp"
#include <string>
#include <vector>
#include <cstdint>
#include <cstddef>
#include <filesystem>

namespace fs = std::filesystem;

struct ChunkMeta {
    int index;
    std::string hash;
    size_t size_bytes;
};

struct FileManifest {
    std::string original_filename;
    size_t total_size;
    size_t chunk_size;
    std::vector<ChunkMeta> chunks;
};

class FileEngine {
public:
    explicit FileEngine(CASStorage& storage);
    FileManifest split_file(const fs::path& filepath, size_t chunk_size = 1024 * 1024);
    void assemble_file(const std::vector<std::string>& chunk_hashes, const fs::path& dest_path);
private:
    CASStorage& cas;
};
