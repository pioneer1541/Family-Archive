#!/usr/bin/env python3
"""
验证第二轮 Review 修复项的脚本
"""

import sys
import re

def check_gitignore():
    """检查 .gitignore 是否包含 tsconfig.tsbuildinfo"""
    with open(".gitignore", "r") as f:
        content = f.read()
    return "tsconfig.tsbuildinfo" in content

def check_url_normalization_tests():
    """检查 URL 规范化测试是否包含 /api 测试"""
    with open("backend/tests/test_ollama_provider_url_normalization.py", "r") as f:
        content = f.read()
    
    checks = [
        "/api" in content,
        "api/" in content,
        "/API" in content,
        "test_normalize_ollama_base_url_strips_api_suffix" in content,
    ]
    return all(checks)

def check_normalize_ollama_base_url():
    """检查 normalize_ollama_base_url 函数是否处理 /api"""
    with open("backend/app/services/llm_provider.py", "r") as f:
        content = f.read()
    
    # 检查函数是否存在
    if "def normalize_ollama_base_url" not in content:
        return False
    
    # 检查是否处理 /api
    return '"/api"' in content or "'/api'" in content

def check_settings_page_data_labels():
    """检查设置页面的表格是否有 data-label"""
    with open("frontend/app/[locale]/settings/page.tsx", "r") as f:
        content = f.read()
    
    # 检查 gmail-cred-table 是否有 data-label
    gmail_table_section = content[content.find("gmail-cred-table"):content.find("gmail-cred-table") + 2000]
    
    # 检查 users-table 是否有 data-label  
    users_table_section = content[content.find("users-table"):content.find("users-table") + 1500]
    
    gmail_has_labels = 'data-label=' in gmail_table_section
    users_has_labels = 'data-label=' in users_table_section
    
    return gmail_has_labels and users_has_labels

def check_model_list_state_handling():
    """检查 model-list 状态处理"""
    with open("frontend/app/[locale]/settings/page.tsx", "r") as f:
        content = f.read()
    
    # 检查是否有 null 检查（加载状态）
    has_null_check = "=== null" in content or "!== null" in content
    
    # 检查是否有错误处理
    has_error_handling = "llmProviderModelErrors" in content
    
    return has_null_check and has_error_handling

def main():
    print("=" * 60)
    print("Family Vault 第二轮 Review 修复验证")
    print("=" * 60)
    
    checks = {
        ".gitignore 包含 tsconfig.tsbuildinfo": check_gitignore,
        "URL 规范化测试包含 /api 测试": check_url_normalization_tests,
        "normalize_ollama_base_url 处理 /api": check_normalize_ollama_base_url,
        "设置页面表格有 data-label": check_settings_page_data_labels,
        "Model-list 状态处理正确": check_model_list_state_handling,
    }
    
    all_passed = True
    for name, check_func in checks.items():
        try:
            result = check_func()
            status = "✅ PASS" if result else "❌ FAIL"
            if not result:
                all_passed = False
        except Exception as e:
            status = f"❌ ERROR: {e}"
            all_passed = False
        print(f"{status}: {name}")
    
    print("=" * 60)
    if all_passed:
        print("所有检查通过！")
        return 0
    else:
        print("部分检查失败，请查看详情。")
        return 1

if __name__ == "__main__":
    sys.exit(main())
