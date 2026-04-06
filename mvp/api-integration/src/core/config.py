"""
配置加载器 - 从.env 文件和环境变量加载配置
"""
import os
from typing import Any, Dict, Optional
from pathlib import Path
from dotenv import load_dotenv


class ConfigLoader:
    """配置加载器"""
    
    def __init__(self, env_file: Optional[str] = None):
        """
        初始化配置加载器
        
        Args:
            env_file: .env 文件路径（默认查找项目根目录）
        """
        if env_file:
            load_dotenv(env_file)
        else:
            # 自动查找 .env 文件
            possible_paths = [
                Path(__file__).parent.parent.parent / ".env",
                Path.cwd() / ".env",
                Path.cwd() / "config" / ".env",
            ]
            
            for path in possible_paths:
                if path.exists():
                    load_dotenv(path)
                    break
        
        self._cache: Dict[str, Any] = {}
    
    def get(self, key: str, default: Any = None, required: bool = False) -> Any:
        """
        获取配置值
        
        Args:
            key: 配置键
            default: 默认值
            required: 是否必需（为 True 时找不到会抛出异常）
        
        Returns:
            配置值
        """
        if key in self._cache:
            return self._cache[key]
        
        value = os.getenv(key)
        
        if value is None:
            if required:
                raise ValueError(f"必需配置缺失：{key}")
            return default
        
        # 类型转换
        if value.lower() in ('true', '1', 'yes'):
            value = True
        elif value.lower() in ('false', '0', 'no'):
            value = False
        elif value.isdigit():
            value = int(value)
        elif self._is_float(value):
            value = float(value)
        
        self._cache[key] = value
        return value
    
    def get_dict(self, prefix: str) -> Dict[str, Any]:
        """
        获取指定前缀的所有配置
        
        Args:
            prefix: 配置前缀（如 'SENDGRID'）
        
        Returns:
            配置字典
        """
        result = {}
        prefix_len = len(prefix) + 1  # +1 for underscore
        
        for key in os.environ:
            if key.startswith(f"{prefix}_"):
                config_key = key[prefix_len:].lower()
                result[config_key] = self.get(key)
        
        return result
    
    def _is_float(self, value: str) -> bool:
        """检查是否为浮点数"""
        try:
            float(value)
            return '.' in value
        except:
            return False


# 全局单例
config = ConfigLoader()
