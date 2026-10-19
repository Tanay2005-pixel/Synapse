#include "chunker.hpp"
#include "cas.hpp"

#include <iostream>
#include <string>
#include <vector>
#include <sstream>
#include <stdexcept>
#include <filesystem>

namespace fs = std::filesystem;

static void print_usage(const char* binary_name) {
    std::cerr << "Usage:\n"
              << "  " << binary_name << " split <sourcefilepath> <storage dir> [chunk size bytes]\n"
              << "  " << binary_name << " merge <dest filepath> <storage dir> <hash1,hash2,hash3...>\n";
}

static std::string escape_json(const std::string& input) {
    std::ostringstream ss;
    for (char c : input) {
        switch (c) {
            case '"':  ss << "\\\""; break;
            case '\\': ss << "\\\\"; break;
            case '\b': ss << "\\b";  break;
            case '\f': ss << "\\f";  break;
            case '\n': ss << "\\n";  break;
            case '\r': ss << "\\r";  break;
            case '\t': ss << "\\t";  break;
            default:   ss << c;       break;
        }
    }
    return ss.str();
}

int main(int argc, char* argv[]) {
    if (argc < 4) {
        print_usage(argv[0]);
        return 1;
    }

    const std::string command = argv[1];

    try {
        if (command == "split") {
            const fs::path filepath = argv[2];
            const fs::path storage_dir = argv[3];

            size_t chunk_size = 1048576;
            if (argc >= 5) {
                chunk_size = std::stoull(argv[4]);
            }

            if (!fs::exists(filepath)) {
                throw std::runtime_error("Source file does not exist: " + filepath.string());
            }

            if (fs::is_directory(filepath)) {
                throw std::runtime_error("Source path is a directory not a file: " + filepath.string());
            }

            CASStorage storage(storage_dir);
            FileEngine engine(storage);
            FileManifest manifest = engine.split_file(filepath, chunk_size);

            std::cout << "{\n"
                      << "  \"status\": \"success\",\n"
                      << "  \"filename\": \"" << escape_json(manifest.original_filename) << "\",\n"
                      << "  \"total_size\": " << manifest.total_size << ",\n"
                      << "  \"chunk_size\": " << manifest.chunk_size << ",\n"
                      << "  \"total_chunks\": " << manifest.chunks.size() << ",\n"
                      << "  \"chunks\": [\n";

            for (size_t i = 0; i < manifest.chunks.size(); ++i) {
                const auto& chunk = manifest.chunks[i];
                std::cout << "    {\n"
                          << "      \"index\": " << chunk.index << ",\n"
                          << "      \"hash\": \"" << chunk.hash << "\",\n"
                          << "      \"size\": " << chunk.size_bytes << "\n"
                          << "    }" << (i + 1 < manifest.chunks.size() ? "," : "") << "\n";
            }

            std::cout << "  ]\n}\n";
            return 0;

        } else if (command == "merge") {
            const fs::path dest_path = argv[2];
            const fs::path storage_dir = argv[3];
            const std::string hashes_csv = argv[4];

            std::vector<std::string> chunk_hashes;
            std::stringstream ss(hashes_csv);
            std::string hash_item;
            while (std::getline(ss, hash_item, ',')) {
                if (!hash_item.empty()) {
                    chunk_hashes.push_back(hash_item);
                }
            }

            if (chunk_hashes.empty()) {
                throw std::runtime_error("No valid chunk hash for merge");
            }

            CASStorage storage(storage_dir);
            FileEngine engine(storage);
            engine.assemble_file(chunk_hashes, dest_path);

            std::cout << "{\n"
                      << "  \"status\": \"success\",\n"
                      << "  \"output\": \"" << escape_json(dest_path.string()) << "\",\n"
                      << "  \"chunks_merged\": " << chunk_hashes.size() << "\n"
                      << "}\n";
            return 0;

        } else {
            std::cerr << "Unknown command: " << command << "\n";
            print_usage(argv[0]);
            return 1;
        }

    } catch (const std::exception& e) {
        std::cerr << "{\n"
                  << "  \"status\": \"error\",\n"
                  << "  \"message\": \"" << escape_json(e.what()) << "\"\n"
                  << "}\n";
        return 1;
    }
}
