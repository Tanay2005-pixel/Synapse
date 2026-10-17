#pragma once

#include <string>
#include <vector>
#include <filesystem>
#include <cstdint>

namespace fs = std::filesystem;

class CASStorage {
public:
    explicit CASStorage(const fs::path& base_dir);
    bool store_chunk(const std::string& hash, const std::vector<uint8_t>& data);
    std::vector<uint8_t> read_chunk(const std::string& hash);
    bool chunk_exists(const std::string& hash) const;
    fs::path get_chunk_path(const std::string& hash) const;

private:
    fs::path root_dir;
    fs::path tmp_dir;
};
