#include "sha256.hpp"

#include <openssl/evp.h>
#include <iomanip>
#include <sstream>
#include <stdexcept>


std::string compute_sha256(const uint8_t* data, size_t length) {
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    if (!ctx)
        throw std::runtime_error("failed to allocate openssl context");

    if (EVP_DigestInit_ex(ctx, EVP_sha256(), nullptr) != 1) 
    {
        EVP_MD_CTX_free(ctx);
        throw std::runtime_error("failed to initialize sha256 digest");
    }

    if (EVP_DigestUpdate(ctx, data, length) != 1) 
    {
        EVP_MD_CTX_free(ctx);
        throw std::runtime_error("Failed to update digest with data");
    }

    uint8_t hash[EVP_MAX_MD_SIZE];
    unsigned int hash_len = 0;

    if (EVP_DigestFinal_ex(ctx, hash, &hash_len) != 1)
    {
        EVP_MD_CTX_free(ctx);
        throw std::runtime_error("Failed to finalize");
    }

    EVP_MD_CTX_free(ctx);

    std::ostringstream os;
    for (unsigned int i = 0; i < hash_len; ++i)
        os << std::hex<< std::setw(2)<< std::setfill('0')<< static_cast<int>(hash[i]);
    

    return os.str();
}

std::string compute_sha256(const std::vector<uint8_t>& buffer) {
    return compute_sha256(buffer.data(), buffer.size());
}
