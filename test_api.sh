#!/bin/bash
# Test script for Optimizarr API

echo "======================================================"
echo "Optimizarr API Test Suite"
echo "======================================================"
echo ""

BASE_URL="http://localhost:5000/api"

# Test 1: Health check (no auth required)
echo "Test 1: Health Check"
curl -s "$BASE_URL/health"
echo -e "\n"

# Test 2: Login
echo "Test 2: Login"
LOGIN_RESPONSE=$(curl -s -X POST "$BASE_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}')
echo "$LOGIN_RESPONSE"
echo ""

# Extract token
TOKEN=$(echo "$LOGIN_RESPONSE" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
echo "Token: ${TOKEN:0:50}..."
echo ""

# Test 3: Get profiles
echo "Test 3: Get Profiles (authenticated)"
curl -s -H "Authorization: Bearer $TOKEN" "$BASE_URL/profiles"
echo -e "\n"

# Test 4: Get scan roots
echo "Test 4: Get Scan Roots (authenticated)"
curl -s -H "Authorization: Bearer $TOKEN" "$BASE_URL/scan-roots"
echo -e "\n"

# Test 5: Get stats
echo "Test 5: Get Stats (authenticated)"
curl -s -H "Authorization: Bearer $TOKEN" "$BASE_URL/stats"
echo -e "\n"

# Test 6: Get queue
echo "Test 6: Get Queue (authenticated)"
curl -s -H "Authorization: Bearer $TOKEN" "$BASE_URL/queue"
echo -e "\n"

echo "======================================================"
echo "All tests completed!"
echo "======================================================"
