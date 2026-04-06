"""
文档 API 集成示例
支持 Google Docs、腾讯文档
"""

from typing import Optional, List, Dict


class GoogleDocsAPI:
    """Google Docs API 集成"""
    
    def __init__(self, credentials_file: str = 'credentials.json'):
        self.credentials_file = credentials_file
        self.service = None
    
    def authenticate(self, scopes: List[str] = None):
        """身份认证"""
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        import os.path
        
        scopes = scopes or ['https://www.googleapis.com/auth/documents']
        
        creds = None
        token_file = 'token_docs.json'
        
        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, scopes)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, scopes
                )
                creds = flow.run_local_server(port=0)
            
            with open(token_file, 'w') as token:
                token.write(creds.to_json())
        
        self.service = build('docs', 'v1', credentials=creds)
        print("✓ Google Docs 认证成功")
    
    def create_document(self, title: str) -> Optional[str]:
        """
        创建文档
        
        Args:
            title: 文档标题
            
        Returns:
            str: 文档 ID
        """
        if not self.service:
            self.authenticate()
        
        try:
            document = self.service.documents().create(body={'title': title}).execute()
            doc_id = document['documentId']
            print(f"✓ 文档已创建：{doc_id}")
            print(f"  访问链接：https://docs.google.com/document/d/{doc_id}")
            return doc_id
            
        except Exception as e:
            print(f"✗ 创建文档失败：{e}")
            return None
    
    def append_text(self, document_id: str, text: str) -> bool:
        """
        追加文本到文档
        
        Args:
            document_id: 文档 ID
            text: 要追加的文本
            
        Returns:
            bool: 操作是否成功
        """
        if not self.service:
            self.authenticate()
        
        try:
            requests = [{
                'insertText': {
                    'location': {
                        'index': -1,
                    },
                    'text': text
                }
            }]
            
            self.service.documents().batchUpdate(
                documentId=document_id,
                body={'requests': requests}
            ).execute()
            
            print(f"✓ 文本已追加到文档：{document_id}")
            return True
            
        except Exception as e:
            print(f"✗ 追加文本失败：{e}")
            return False
    
    def get_document(self, document_id: str) -> Optional[Dict]:
        """
        获取文档内容
        
        Args:
            document_id: 文档 ID
            
        Returns:
            dict: 文档内容
        """
        if not self.service:
            self.authenticate()
        
        try:
            document = self.service.documents().get(documentId=document_id).execute()
            print(f"✓ 文档已获取：{document.get('title')}")
            return document
            
        except Exception as e:
            print(f"✗ 获取文档失败：{e}")
            return None
    
    def delete_document(self, document_id: str) -> bool:
        """
        删除文档
        
        Args:
            document_id: 文档 ID
            
        Returns:
            bool: 删除是否成功
        """
        from googleapiclient.errors import HttpError
        
        if not self.service:
            self.authenticate()
        
        try:
            # Google Docs API 没有直接删除接口，需要通过 Drive API
            from googleapiclient.discovery import build
            drive_service = build('drive', 'v3', credentials=self.service._http._credentials)
            drive_service.files().delete(fileId=document_id).execute()
            print(f"✓ 文档已删除：{document_id}")
            return True
            
        except Exception as e:
            print(f"✗ 删除文档失败：{e}")
            return False


class TencentDocsAPI:
    """腾讯文档 API 集成（模拟示例，实际需接入腾讯文档开放平台）"""
    
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.access_token = None
        self.base_url = "https://docs.qq.com/api"
    
    def get_access_token(self) -> Optional[str]:
        """获取访问令牌"""
        import requests
        
        try:
            response = requests.post(
                f"{self.base_url}/oauth/token",
                data={
                    'grant_type': 'client_credentials',
                    'app_id': self.app_id,
                    'app_secret': self.app_secret
                }
            )
            
            result = response.json()
            self.access_token = result.get('access_token')
            print(f"✓ 腾讯文档访问令牌已获取")
            return self.access_token
            
        except Exception as e:
            print(f"✗ 获取访问令牌失败：{e}")
            return None
    
    def create_sheet(self, title: str) -> Optional[str]:
        """
        创建在线表格
        
        Args:
            title: 表格标题
            
        Returns:
            str: 表格 ID
        """
        import requests
        
        if not self.access_token:
            self.get_access_token()
        
        try:
            headers = {'Authorization': f'Bearer {self.access_token}'}
            response = requests.post(
                f"{self.base_url}/sheet/create",
                headers=headers,
                json={'title': title}
            )
            
            result = response.json()
            sheet_id = result.get('sheet_id')
            print(f"✓ 腾讯表格已创建：{sheet_id}")
            return sheet_id
            
        except Exception as e:
            print(f"✗ 创建表格失败：{e}")
            return None
    
    def update_cells(self, sheet_id: str, data: List[List[str]]) -> bool:
        """
        更新单元格数据
        
        Args:
            sheet_id: 表格 ID
            data: 二维数组数据
            
        Returns:
            bool: 更新是否成功
        """
        import requests
        
        if not self.access_token:
            self.get_access_token()
        
        try:
            headers = {'Authorization': f'Bearer {self.access_token}'}
            response = requests.post(
                f"{self.base_url}/sheet/{sheet_id}/cells",
                headers=headers,
                json={'values': data}
            )
            
            result = response.json()
            if result.get('code') == 0:
                print(f"✓ 表格数据已更新")
                return True
            else:
                print(f"✗ 表格数据更新失败：{result}")
                return False
            
        except Exception as e:
            print(f"✗ 更新表格失败：{e}")
            return False


# 使用示例
if __name__ == "__main__":
    # Google Docs
    docs = GoogleDocsAPI()
    docs.authenticate()
    
    doc_id = docs.create_document("项目需求文档")
    docs.append_text(doc_id, "# 项目需求\n\n## 1. 项目概述\n\n")
    
    # 腾讯文档
    tencent = TencentDocsAPI(
        app_id="YOUR_APP_ID",
        app_secret="YOUR_APP_SECRET"
    )
    
    sheet_id = tencent.create_sheet("项目进度跟踪表")
    tencent.update_cells(sheet_id, [
        ["任务", "负责人", "状态", "截止日期"],
        ["需求分析", "张三", "已完成", "2026-03-10"],
        ["系统设计", "李四", "进行中", "2026-03-15"]
    ])
