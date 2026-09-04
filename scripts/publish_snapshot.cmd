@echo off
rem 盘前选股系统 — 每日发布行情数据快照到 GitHub data-snapshot 分支（本机权威源）
rem 由 Windows 计划任务 \StockPlan\Snapshot_2130 调用（21:30，晚于 20:00 增量更新完成）
cd /d "D:\Vscode\stock plan\Text"
if not exist "data\logs" mkdir "data\logs"
".venv\Scripts\python.exe" scripts\publish_snapshot.py >> "data\logs\snapshot.log" 2>&1
