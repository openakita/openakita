"""
GitHub API 客户端
支持仓库管理、Issue 管理、PR 管理、Webhook 等功能
文档：https://docs.github.com/en/rest
"""
from typing import List, Optional, Dict, Any
from .base import BaseAPIClient, APIError
import structlog

logger = structlog.get_logger()


class GitHubClient(BaseAPIClient):
    """GitHub API 客户端"""
    
    def __init__(self, token: str, base_url: str = "https://api.github.com"):
        super().__init__(
            base_url=base_url,
            api_key=token,
            timeout=30
        )
        self.token = token
    
    def _get_auth_header(self) -> str:
        """GitHub 使用 Bearer Token 认证"""
        return f"Bearer {self.token}"
    
    async def test_auth(self) -> bool:
        """测试认证是否有效"""
        try:
            response = await self.get("/user")
            return "login" in response
        except APIError:
            return False
    
    async def get_repo(self, owner: str, repo: str) -> Dict[str, Any]:
        """获取仓库信息"""
        response = await self.get(f"/repos/{owner}/{repo}")
        return response
    
    async def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: Optional[str] = None,
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        创建 Issue
        
        Args:
            owner: 仓库所有者
            repo: 仓库名称
            title: Issue 标题
            body: Issue 描述
            labels: 标签列表
            assignees: 指派人员列表
            
        Returns:
            创建的 Issue 信息
        """
        data = {
            "title": title,
            "body": body or "",
        }
        
        if labels:
            data["labels"] = labels
        if assignees:
            data["assignees"] = assignees
        
        response = await self.post(f"/repos/{owner}/{repo}/issues", json_data=data)
        
        logger.info("github_issue_created", repo=f"{owner}/{repo}", issue_number=response.get("number"))
        return response
    
    async def get_issue(self, owner: str, repo: str, issue_number: int) -> Dict[str, Any]:
        """获取 Issue 详情"""
        response = await self.get(f"/repos/{owner}/{repo}/issues/{issue_number}")
        return response
    
    async def update_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
        labels: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """更新 Issue"""
        data = {}
        if title:
            data["title"] = title
        if body:
            data["body"] = body
        if state:
            data["state"] = state
        if labels:
            data["labels"] = labels
        
        response = await self.patch(f"/repos/{owner}/{repo}/issues/{issue_number}", json_data=data)
        return response
    
    async def close_issue(self, owner: str, repo: str, issue_number: int) -> Dict[str, Any]:
        """关闭 Issue"""
        return await self.update_issue(owner, repo, issue_number, state="closed")
    
    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        创建 Pull Request
        
        Args:
            owner: 仓库所有者
            repo: 仓库名称
            title: PR 标题
            head: 源分支
            base: 目标分支
            body: PR 描述
            
        Returns:
            创建的 PR 信息
        """
        data = {
            "title": title,
            "head": head,
            "base": base,
            "body": body or ""
        }
        
        response = await self.post(f"/repos/{owner}/{repo}/pulls", json_data=data)
        
        logger.info("github_pr_created", repo=f"{owner}/{repo}", pr_number=response.get("number"))
        return response
    
    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> Dict[str, Any]:
        """获取 PR 详情"""
        response = await self.get(f"/repos/{owner}/{repo}/pulls/{pr_number}")
        return response
    
    async def merge_pull_request(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_title: Optional[str] = None,
        commit_message: Optional[str] = None,
        merge_method: str = "merge"
    ) -> Dict[str, Any]:
        """
        合并 PR
        
        Args:
            merge_method: 合并方式 (merge/squash/rebase)
        """
        data = {
            "merge_method": merge_method
        }
        if commit_title:
            data["commit_title"] = commit_title
        if commit_message:
            data["commit_message"] = commit_message
        
        response = await self.put(f"/repos/{owner}/{repo}/pulls/{pr_number}/merge", json_data=data)
        return response
    
    async def create_webhook(
        self,
        owner: str,
        repo: str,
        url: str,
        events: Optional[List[str]] = None,
        secret: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        创建 Webhook
        
        Args:
            url: Webhook URL
            events: 触发事件列表
            secret: 密钥
        """
        data = {
            "name": "web",
            "active": True,
            "events": events or ["push", "pull_request", "issues"],
            "config": {
                "url": url,
                "content_type": "json"
            }
        }
        
        if secret:
            data["config"]["secret"] = secret
        
        response = await self.post(f"/repos/{owner}/{repo}/hooks", json_data=data)
        
        logger.info("github_webhook_created", repo=f"{owner}/{repo}", webhook_id=response.get("id"))
        return response
    
    async def list_webhooks(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        """列出所有 Webhook"""
        response = await self.get(f"/repos/{owner}/{repo}/hooks")
        return response
    
    async def delete_webhook(self, owner: str, repo: str, webhook_id: int) -> Dict[str, Any]:
        """删除 Webhook"""
        response = await self.delete(f"/repos/{owner}/{repo}/hooks/{webhook_id}")
        return response
    
    async def get_user_repos(self, username: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取用户仓库列表"""
        url = f"/users/{username}/repos" if username else "/user/repos"
        response = await self.get(url)
        return response
    
    async def create_repo(
        self,
        name: str,
        description: Optional[str] = None,
        private: bool = False,
        auto_init: bool = True
    ) -> Dict[str, Any]:
        """
        创建仓库
        
        Args:
            name: 仓库名称
            description: 描述
            private: 是否私有
            auto_init: 是否初始化 README
        """
        data = {
            "name": name,
            "private": private,
            "auto_init": auto_init
        }
        
        if description:
            data["description"] = description
        
        response = await self.post("/user/repos", json_data=data)
        
        logger.info("github_repo_created", repo=name)
        return response


# 使用示例
async def example_usage():
    """GitHub API 使用示例"""
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("❌ 请设置 GITHUB_TOKEN 环境变量")
        return
    
    async with GitHubClient(token) as client:
        # 测试认证
        is_valid = await client.test_auth()
        print(f"✅ 认证有效：{is_valid}")
        
        # 获取仓库信息
        repo = await client.get_repo("openakita", "openakita")
        print(f"📦 仓库：{repo.get('full_name')}")
        
        # 创建 Issue
        issue = await client.create_issue(
            owner="openakita",
            repo="openakita",
            title="测试 Issue",
            body="这是通过 API 创建的测试 Issue",
            labels=["bug"]
        )
        print(f"🐛 创建 Issue: #{issue.get('number')}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(example_usage())
