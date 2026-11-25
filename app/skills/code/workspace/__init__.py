# # app/skills/code/workspace/__init__.py
# from app.skills.code.workspace.service import code_workspace_service
# from app.skills.code.workspace.scan_service import code_scan_service
#
# __all__ = ["code_workspace_service", "code_scan_service"]

# app/skills/code/workspace/__init__.py
# 故意留空：避免 import 子模块时触发 scan_service 的线程池/Redis 初始化副作用。