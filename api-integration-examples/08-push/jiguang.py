"""
API 集成示例 8: 极光推送
"""
import requests
import base64

class JiguangPushClient:
    def __init__(self, app_key, master_secret):
        self.app_key = app_key
        self.master_secret = master_secret
        self.base_url = "https://api.jpush.cn/v3"
        self.auth = base64.b64encode(f"{master_secret}:".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {self.auth}",
            "Content-Type": "application/json"
        }
    
    def push_notification(self, alias, title, content, extras=None):
        """推送通知"""
        data = {
            "platform": "all",
            "audience": {"alias": [alias]},
            "notification": {
                "alert": content,
                "android": {
                    "title": title,
                    "extras": extras or {}
                },
                "ios": {
                    "alert": content,
                    "extras": extras or {},
                    "badge": "+1"
                }
            },
            "options": {
                "time_to_live": 86400,
                "apns_production": False
            }
        }
        
        response = requests.post(
            f"{self.base_url}/push",
            json=data,
            headers=self.headers
        )
        
        return response.status_code == 200
    
    def push_to_all(self, title, content):
        """推送给所有用户"""
        data = {
            "platform": "all",
            "audience": "all",
            "notification": {
                "alert": content,
                "android": {"title": title},
                "ios": {"alert": content, "badge": "+1"}
            }
        }
        
        response = requests.post(
            f"{self.base_url}/push",
            json=data,
            headers=self.headers
        )
        
        return response.status_code == 200

# 使用示例
if __name__ == "__main__":
    jiguang = JiguangPushClient("app_key", "master_secret")
    # jiguang.push_notification("user123", "通知标题", "通知内容")
    print("极光推送示例已就绪")
