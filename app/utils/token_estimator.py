def estimate_tokens(text: str) -> int:
    """粗略估算 token 数"""
    return len(text) // 2