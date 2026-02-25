#!/usr/bin/env python3
"""
文件操作脚本

用法:
    python file_ops.py <command> [options]

命令:
    list    列出目录内容
    read    读取文件
    write   写入文件
    copy    复制文件
    move    移动/重命名文件
    delete  删除文件或目录
    search  搜索文件
    info    获取文件信息
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def format_size(size: int) -> str:
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def list_directory(path: str, recursive: bool = False, pattern: str = None) -> dict:
    """列出目录内容"""
    p = Path(path)
    if not p.exists():
        return {"success": False, "error": f"Path not found: {path}"}
    if not p.is_dir():
        return {"success": False, "error": f"Not a directory: {path}"}
    
    files = []
    directories = []
    
    if recursive:
        items = p.rglob(pattern or "*")
    else:
        items = p.glob(pattern or "*")
    
    for item in items:
        if item.is_file():
            files.append(str(item.relative_to(p)))
        elif item.is_dir():
            directories.append(str(item.relative_to(p)))
    
    return {
        "success": True,
        "operation": "list",
        "path": str(p.absolute()),
        "data": {
            "files": sorted(files),
            "directories": sorted(directories),
            "file_count": len(files),
            "dir_count": len(directories),
        }
    }


def read_file(path: str, encoding: str = "utf-8") -> dict:
    """读取文件内容"""
    p = Path(path)
    if not p.exists():
        return {"success": False, "error": f"File not found: {path}"}
    if not p.is_file():
        return {"success": False, "error": f"Not a file: {path}"}
    
    try:
        content = p.read_text(encoding=encoding)
        return {
            "success": True,
            "operation": "read",
            "path": str(p.absolute()),
            "data": {
                "content": content,
                "size": len(content),
                "lines": content.count('\n') + 1,
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def write_file(path: str, content: str, append: bool = False, encoding: str = "utf-8") -> dict:
    """写入文件"""
    p = Path(path)
    
    try:
        # 确保目录存在
        p.parent.mkdir(parents=True, exist_ok=True)
        
        mode = "a" if append else "w"
        with open(p, mode, encoding=encoding) as f:
            f.write(content)
        
        return {
            "success": True,
            "operation": "write",
            "path": str(p.absolute()),
            "data": {
                "bytes_written": len(content.encode(encoding)),
                "append": append,
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def copy_file(source: str, destination: str) -> dict:
    """复制文件或目录"""
    src = Path(source)
    dst = Path(destination)
    
    if not src.exists():
        return {"success": False, "error": f"Source not found: {source}"}
    
    try:
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        
        return {
            "success": True,
            "operation": "copy",
            "data": {
                "source": str(src.absolute()),
                "destination": str(dst.absolute()),
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def move_file(source: str, destination: str) -> dict:
    """移动/重命名文件或目录"""
    src = Path(source)
    dst = Path(destination)
    
    if not src.exists():
        return {"success": False, "error": f"Source not found: {source}"}
    
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dst)
        
        return {
            "success": True,
            "operation": "move",
            "data": {
                "source": str(src.absolute()),
                "destination": str(dst.absolute()),
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def delete_path(path: str, recursive: bool = False) -> dict:
    """删除文件或目录"""
    p = Path(path)
    
    if not p.exists():
        return {"success": False, "error": f"Path not found: {path}"}
    
    try:
        if p.is_dir():
            if recursive:
                shutil.rmtree(p)
            else:
                p.rmdir()  # 只能删除空目录
        else:
            p.unlink()
        
        return {
            "success": True,
            "operation": "delete",
            "data": {
                "path": str(p.absolute()),
                "was_directory": p.is_dir() if p.exists() else None,
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def search_files(directory: str, pattern: str, content: str = None) -> dict:
    """搜索文件"""
    p = Path(directory)
    
    if not p.exists():
        return {"success": False, "error": f"Directory not found: {directory}"}
    
    matches = []
    
    for file_path in p.rglob(pattern):
        if not file_path.is_file():
            continue
        
        match_info = {
            "path": str(file_path.relative_to(p)),
            "size": file_path.stat().st_size,
        }
        
        # 如果指定了内容搜索
        if content:
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                if content.lower() in text.lower():
                    # 找到匹配的行
                    lines = []
                    for i, line in enumerate(text.split('\n'), 1):
                        if content.lower() in line.lower():
                            lines.append({"line": i, "content": line.strip()[:100]})
                    match_info["matches"] = lines[:10]  # 最多10个匹配
                    matches.append(match_info)
            except Exception:
                pass
        else:
            matches.append(match_info)
    
    return {
        "success": True,
        "operation": "search",
        "data": {
            "directory": str(p.absolute()),
            "pattern": pattern,
            "content_search": content,
            "matches": matches,
            "count": len(matches),
        }
    }


def get_info(path: str) -> dict:
    """获取文件/目录信息"""
    p = Path(path)
    
    if not p.exists():
        return {"success": False, "error": f"Path not found: {path}"}
    
    stat = p.stat()
    
    info = {
        "path": str(p.absolute()),
        "name": p.name,
        "is_file": p.is_file(),
        "is_directory": p.is_dir(),
        "size": stat.st_size,
        "size_formatted": format_size(stat.st_size),
        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "accessed": datetime.fromtimestamp(stat.st_atime).isoformat(),
    }
    
    if p.is_file():
        info["extension"] = p.suffix
    elif p.is_dir():
        items = list(p.iterdir())
        info["item_count"] = len(items)
        info["file_count"] = sum(1 for i in items if i.is_file())
        info["dir_count"] = sum(1 for i in items if i.is_dir())
    
    return {
        "success": True,
        "operation": "info",
        "data": info,
    }


def main():
    parser = argparse.ArgumentParser(description="File Manager")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # list
    list_parser = subparsers.add_parser("list", help="List directory contents")
    list_parser.add_argument("path", help="Directory path")
    list_parser.add_argument("--recursive", "-r", action="store_true")
    list_parser.add_argument("--pattern", "-p", help="Glob pattern")
    
    # read
    read_parser = subparsers.add_parser("read", help="Read file")
    read_parser.add_argument("path", help="File path")
    read_parser.add_argument("--encoding", "-e", default="utf-8")
    
    # write
    write_parser = subparsers.add_parser("write", help="Write file")
    write_parser.add_argument("path", help="File path")
    write_parser.add_argument("--content", "-c", required=True)
    write_parser.add_argument("--append", "-a", action="store_true")
    write_parser.add_argument("--encoding", "-e", default="utf-8")
    
    # copy
    copy_parser = subparsers.add_parser("copy", help="Copy file/directory")
    copy_parser.add_argument("source", help="Source path")
    copy_parser.add_argument("destination", help="Destination path")
    
    # move
    move_parser = subparsers.add_parser("move", help="Move/rename file/directory")
    move_parser.add_argument("source", help="Source path")
    move_parser.add_argument("destination", help="Destination path")
    
    # delete
    delete_parser = subparsers.add_parser("delete", help="Delete file/directory")
    delete_parser.add_argument("path", help="Path to delete")
    delete_parser.add_argument("--recursive", "-r", action="store_true")
    
    # search
    search_parser = subparsers.add_parser("search", help="Search files")
    search_parser.add_argument("directory", help="Directory to search")
    search_parser.add_argument("--pattern", "-p", required=True, help="Glob pattern")
    search_parser.add_argument("--content", "-c", help="Content to search for")
    
    # info
    info_parser = subparsers.add_parser("info", help="Get file/directory info")
    info_parser.add_argument("path", help="Path")
    
    args = parser.parse_args()
    
    if args.command == "list":
        result = list_directory(args.path, args.recursive, args.pattern)
    elif args.command == "read":
        result = read_file(args.path, args.encoding)
    elif args.command == "write":
        result = write_file(args.path, args.content, args.append, args.encoding)
    elif args.command == "copy":
        result = copy_file(args.source, args.destination)
    elif args.command == "move":
        result = move_file(args.source, args.destination)
    elif args.command == "delete":
        result = delete_path(args.path, args.recursive)
    elif args.command == "search":
        result = search_files(args.directory, args.pattern, args.content)
    elif args.command == "info":
        result = get_info(args.path)
    else:
        result = {"success": False, "error": f"Unknown command: {args.command}"}
    
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
