# 漏洞与缺陷清单

> 状态说明：未修复 / 已修复
> 后续修复按编号引用，修复后更新状态并注明提交

## BUG-001 已修复（6a1873d）
- 位置：backend/app/main.py:141
- 描述：SPA fallback 路径穿越漏洞，`frontend_dist / full_path` 未校验，可通过 `//etc/passwd` 或编码穿越读取服务器任意文件

## BUG-002 已修复（6a1873d）
- 位置：backend/app/routers/recordings.py:439-447
- 描述：WS 收尾时 `transcribe_task.cancel()` 无法终止已进入 to_thread 的解码线程，与收尾转写并发写同一 raw 文件，PCM 数据损坏

## BUG-003 已修复（6a1873d）
- 位置：backend/app/main.py:57-75
- 描述：僵死录制清理误杀安静课堂，转写文本仅在新增时落库，静音超 10 分钟时录制中的记录被误标 FAILED

## BUG-004 已修复（25c9178）
- 位置：frontend/src/pages/LiveRecorder.tsx:292
- 描述：ws.onclose 捕获的 paused 为创建时的陈旧快照（恒 false），暂停状态下 WS 断开时误清 sessionStorage 暂停标记，刷新恢复横幅失效

## BUG-005 已修复（25c9178）
- 位置：frontend/src/pages/RecordingDetail.tsx:63
- 描述：生成分享链接后列表刷新触发 share_expires_at 变化，effect 误重置面板状态，刚生成的链接立即被清空无法复制

## BUG-006 已修复（25c9178）
- 位置：frontend/src/pages/LiveRecorder.tsx:330-355
- 描述：暂停/恢复未停启计时器，录制时长与持久化的 elapsed 包含暂停时间

## BUG-007 已修复（25c9178）
- 位置：backend/app/routers/recordings.py:129-147
- 描述：REST finish 不感知活跃 WS 会话且 finalize 不检查当前状态，可能双重总结并互相覆盖状态

## BUG-008 已修复（25c9178）
- 位置：backend/app/routers/recordings.py:512
- 描述：删除记录不清理音频文件；合并仅清理主文件与 .raw，暂停/重连产生的分片文件与 tagged raw 残留磁盘泄漏

## BUG-009 已修复（25c9178）
- 位置：frontend/src/App.tsx:44-52
- 描述：列表轮询一次失败后 setTimeout 链永久断裂，处理中状态卡死

## BUG-010 已修复（25c9178）
- 位置：backend/app/routers/recordings.py:326
- 描述：同一记录无并发 WS 互斥，第二个连接在转写为空时以 "wb" 截断同一音频文件

## BUG-011 已修复（25c9178）
- 位置：backend/app/routers/recordings.py:349
- 描述：首段音频尚无转写文本时刷新重连，resumed 判断为 False，原音频文件被新会话截断

## BUG-012 已修复（25c9178）
- 位置：frontend/src/pages/RecordingDetail.tsx:230
- 描述：手动总结失败错误写入 shareError，分享面板未打开时错误不可见

## BUG-013 已修复（25c9178）
- 位置：backend/app/routers/recordings.py:375
- 描述：转写循环中 send_json 异常未捕获，循环静默死亡，录制继续但不再更新转写

## BUG-014 已修复（25c9178）
- 位置：frontend/src/pages/LiveRecorder.tsx:89
- 描述：记录已删除（getRecording 404）时 sessionStorage 键未清理，残留至标签页关闭

## BUG-015 已修复（25c9178）
- 位置：backend/app/pipeline.py:134
- 描述：每次重连恢复产生一个新 .raw 文件且永不清理，磁盘缓慢增长

## BUG-016 已修复
- 位置：backend/app/routers/recordings.py、frontend/src/App.tsx
- 描述：存在处理中记录时，前端每 3 秒轮询记录列表，而旧列表接口返回所有历史记录的完整转写与总结；实时 WebSocket 还周期性重发不断增长的完整转写。当前数据量下单次列表文本净荷约 944 KiB，产生远高于音频上传的重复下行流量。修复后新增 `/api/recordings/compact`，仅返回轻量元数据和 `has_summary` 标记，完整正文仅在打开详情或处理状态变化时按需加载；新版前端通过协商使用“公共前缀位置 + 修订尾部”的转写补丁消息。原完整列表与全量转写协议继续保留，兼容尚未刷新的旧客户端。

## BUG-017 已修复（a617876）
- 位置：frontend/src/pages/RecordingDetail.tsx
- 描述：移动端转写原文没有独立滚动容器，“跳转到底部”仍调用元素内部滚动，点击后页面不会移动；现根据原文是否可独立滚动，在桌面端滚动原文容器，在移动端滚动页面至最后一段。

## BUG-018 已修复
- 位置：backend/app/routers/recordings.py
- 描述：前端列表已改为请求轻量接口 `/api/recordings/compact`，但后端缺少该路由，请求被 `/{recording_id}` 动态路由接管并返回 422；前端刷新失败静默处理，导致历史记录列表看似为空。恢复轻量列表路由，返回当前用户的记录元数据而不传输转写与总结正文。
