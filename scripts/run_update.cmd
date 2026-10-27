@echo off
rem 盘前选股系统 — 每日定时增量数据更新（由 Windows 计划任务调用）
rem 时间点：每个交易日 8:00 / 20:00（幂等，重复运行无副作用）
cd /d "D:\Vscode\stock plan\Text"
if not exist "data\logs" mkdir "data\logs"
".venv\Scripts\python.exe" -m stock_plan.data.update_daily >> "data\logs\schtasks.log" 2>&1
