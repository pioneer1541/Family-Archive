#!/usr/bin/env python3
"""
验证第二轮 review 修复的代码逻辑
"""
import sys
sys.path.insert(0, '/Users/openclaw/.openclaw/workspace-coder/family-vault/backend')

# 1. 验证 Ollama URL normalization 处理 /api
def test_normalize_ollama_base_url():
    from app.services.llm_provider import normalize_ollama_base_url
    
    # Basic URLs
    assert normalize_ollama_base_url("http://192.168.1.162:11434") == "http://192.168.1.162:11434"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/") == "http://192.168.1.162:11434"
    
    # /v1 suffix removal
    assert normalize_ollama_base_url("http://192.168.1.162:11434/v1") == "http://192.168.1.162:11434"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/v1/") == "http://192.168.1.162:11434"
    
    # /api suffix removal (关键测试)
    assert normalize_ollama_base_url("http://192.168.1.162:11434/api") == "http://192.168.1.162:11434"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/api/") == "http://192.168.1.162:11434"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/API") == "http://192.168.1.162:11434"  # case insensitive
    assert normalize_ollama_base_url("http://192.168.1.162:11434/Api/") == "http://192.168.1.162:11434"
    
    # Combined suffixes
    assert normalize_ollama_base_url("http://192.168.1.162:11434/v1/api/") == "http://192.168.1.162:11434"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/api/v1") == "http://192.168.1.162:11434"
    
    # Nested paths with /api
    assert normalize_ollama_base_url("http://192.168.1.162:11434/custom/api") == "http://192.168.1.162:11434/custom"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/custom/api/") == "http://192.168.1.162:11434/custom"
    
    print("✅ Ollama URL normalization 测试通过")

# 2. 验证 .gitignore 包含 tsconfig.tsbuildinfo
def test_gitignore():
    with open('/Users/openclaw/.openclaw/workspace-coder/family-vault/.gitignore', 'r') as f:
        content = f.read()
    
    assert 'tsconfig.tsbuildinfo' in content, "tsconfig.tsbuildinfo 应该在 .gitignore 中"
    print("✅ .gitignore 包含 tsconfig.tsbuildinfo")

if __name__ == "__main__":
    print("验证第二轮 review 修复...\n")
    
    try:
        test_normalize_ollama_base_url()
    except Exception as e:
        print(f"❌ Ollama URL normalization 测试失败: {e}")
    
    try:
        test_gitignore()
    except Exception as e:
        print(f"❌ .gitignore 测试失败: {e}")
    
    print("\n验证完成!")
