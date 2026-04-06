"""
认证管理器 - 安全存储和管理 API 凭据
"""
import os
import json
import base64
from typing import Optional, Dict
from cryptography.fernet import Fernet
from pathlib import Path


class CredentialManager:
    """凭据管理器"""
    
    def __init__(self, encryption_key: Optional[str] = None):
        """
        初始化凭据管理器
        
        Args:
            encryption_key: 加密密钥（64 位 base64 编码），如不提供则从环境变量读取
        """
        self.encryption_key = encryption_key or os.getenv("CREDENTIAL_ENCRYPTION_KEY")
        
        if not self.encryption_key:
            # 开发模式：不加密
            self.cipher = None
            print("⚠️  警告：未设置加密密钥，凭据将以明文存储（仅限开发环境）")
        else:
            self.cipher = Fernet(self.encryption_key.encode())
        
        self.credentials_file = Path(os.getenv("CREDENTIALS_FILE", "credentials.json"))
        self._cache: Dict[str, str] = {}
    
    def get(self, service: str, key: str) -> Optional[str]:
        """
        获取凭据
        
        Args:
            service: 服务名称（如 'sendgrid', 'alipay'）
            key: 凭据键（如 'api_key', 'secret'）
        
        Returns:
            凭据值（已解密）
        """
        cache_key = f"{service}:{key}"
        
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        if not self.credentials_file.exists():
            return None
        
        try:
            with open(self.credentials_file, 'r', encoding='utf-8') as f:
                credentials = json.load(f)
            
            encrypted_value = credentials.get(service, {}).get(key)
            if not encrypted_value:
                return None
            
            if self.cipher:
                # 解密
                value = self.cipher.decrypt(encrypted_value.encode()).decode()
            else:
                # 明文
                value = encrypted_value
            
            self._cache[cache_key] = value
            return value
            
        except Exception as e:
            print(f"读取凭据失败：{e}")
            return None
    
    def set(self, service: str, key: str, value: str) -> None:
        """
        保存凭据
        
        Args:
            service: 服务名称
            key: 凭据键
            value: 凭据值（将自动加密）
        """
        credentials = {}
        
        if self.credentials_file.exists():
            try:
                with open(self.credentials_file, 'r', encoding='utf-8') as f:
                    credentials = json.load(f)
            except:
                credentials = {}
        
        if service not in credentials:
            credentials[service] = {}
        
        if self.cipher:
            # 加密存储
            credentials[service][key] = self.cipher.encrypt(value.encode()).decode()
        else:
            # 明文存储
            credentials[service][key] = value
        
        # 确保目录存在
        self.credentials_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.credentials_file, 'w', encoding='utf-8') as f:
            json.dump(credentials, f, indent=2, ensure_ascii=False)
        
        # 更新缓存
        self._cache[f"{service}:{key}"] = value
    
    def remove(self, service: str, key: Optional[str] = None) -> None:
        """
        删除凭据
        
        Args:
            service: 服务名称
            key: 凭据键（如不提供则删除该服务所有凭据）
        """
        if not self.credentials_file.exists():
            return
        
        try:
            with open(self.credentials_file, 'r', encoding='utf-8') as f:
                credentials = json.load(f)
            
            if service in credentials:
                if key:
                    if key in credentials[service]:
                        del credentials[service][key]
                else:
                    del credentials[service]
            
            with open(self.credentials_file, 'w', encoding='utf-8') as f:
                json.dump(credentials, f, indent=2, ensure_ascii=False)
            
            # 清理缓存
            cache_keys = [k for k in self._cache if k.startswith(f"{service}:")]
            for k in cache_keys:
                del self._cache[k]
                
        except Exception as e:
            print(f"删除凭据失败：{e}")
    
    @staticmethod
    def generate_key() -> str:
        """生成新的加密密钥"""
        return Fernet.generate_key().decode()


# 全局单例
credential_manager = CredentialManager()
