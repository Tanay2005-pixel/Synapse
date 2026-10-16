#pragma once

#include <string>
#include <vector>
#include <cstdint>
#include <cstddef>

std::string compute_sha256(const uint8_t* data,size_t len);
std::string compute_sha256(const std::vector<uint8_t>& buffer);
