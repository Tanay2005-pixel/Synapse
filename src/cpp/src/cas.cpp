#include "cas.hpp"
#include <fstream>
#include <stdexcept>
#include <random>

CASStorage::CASStorage(const fs::path& base_dir) : root_dir(base_dir)
{
    tmp_dir = root_dir / "tmp";
    fs::create_directories(tmp_dir);
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

bool CASStorage::store_chunk(const std::string& hash, const std::vector<uint8_t>& data) {
    fs::path target_path = get_chunk_path(hash);

    if (fs::exists(target_path))
        return false;

    fs::create_directories(target_path.parent_path());

    std::random_device rd;
    fs::path temp_file = tmp_dir / (hash + "_" + std::to_string(rd()));

    {
        std::ofstream out(temp_file, std::ios::binary);
        if (!out)
            throw std::runtime_error("temp chunk file can't be opened " + temp_file.string());
        out.write(reinterpret_cast<const char*>(data.data()), data.size());
    }    
    std::error_code ec;
    fs::create_hard_link(temp_file,target_path,ec);

    std::error_code ec2;
    fs::remove(temp_file,ec2);
    if(ec)
    {
        return false;
    }
    return true;
}

std::vector<uint8_t> CASStorage::read_chunk(const std::string& hash) {
    fs::path target = get_chunk_path(hash);

    std::ifstream in(target, std::ios::binary);
    if (!in)
        throw std::runtime_error("Chunk failure " + hash);

    in.seekg(0, std::ios::end);
    size_t size = in.tellg();
    in.seekg(0, std::ios::beg);

    std::vector<uint8_t> buffer(size);
    in.read(reinterpret_cast<char*>(buffer.data()), size);

    return buffer;
}
