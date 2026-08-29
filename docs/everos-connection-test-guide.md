# EverOS 服务器连接测试指南

## 1. SSH 连接测试

### 使用指定密钥连接到服务器
```bash
ssh -i "SSH Secure Brain" user@your_server_ip
```

### 连接后测试EverOS服务状态
```bash
# 检查EverOS服务是否运行
curl http://127.0.0.1:8000/health

# 或者检查进程
ps aux | grep everos
```

## 2. 本地测试脚本使用

### 运行Python测试脚本
```bash
cd D:\第二大脑\scripts
python test_everos_connection.py
```

### 直接使用curl测试API

#### 测试健康检查
```bash
curl http://your_server_ip:8000/health
```

#### 测试记忆检索
```bash
curl -X POST http://your_server_ip:8000/api/v2/memory/search \
  -H "Content-Type: application/json" \
  -d '{"query": "认知思维"}'
```

#### 测试记忆添加
```bash
curl -X POST http://your_server_ip:8000/api/v2/memory/add \
  -H "Content-Type: application/json" \
  -d '{
    "content": "这是一个测试记忆条目",
    "metadata": {
      "type": "test",
      "source": "manual_test",
      "timestamp": "2026-08-14"
    }
  }'
```

#### 测试记忆固化
```bash
curl -X POST http://your_server_ip:8000/api/v2/memory/flush
```

## 3. 通过SSH隧道测试

### 建立SSH隧道
```bash
ssh -i "SSH Secure Brain" -L 8000:127.0.0.1:8000 user@your_server_ip
```

### 然后在本地测试
```bash
# 测试本地端口转发
curl http://127.0.0.1:8000/health

# 运行Python测试脚本（选择本地测试模式）
python test_everos_connection.py
```

## 4. 验证记忆功能

### 添加测试记忆
```bash
curl -X POST http://your_server_ip:8000/api/v2/memory/add \
  -H "Content-Type: application/json" \
  -d '{
    "content": "大聪明认知思维定位模型包含小农思维、富人思维、强者思维三个层级",
    "metadata": {
      "type": "concept",
      "source": "人智55篇",
      "article_no": "25",
      "confidence": 0.9
    }
  }'
```

### 检索添加的记忆
```bash
curl -X POST http://your_server_ip:8000/api/v2/memory/search \
  -H "Content-Type: application/json" \
  -d '{"query": "大聪明思维"}'
```

## 5. 故障排查

### 如果连接失败
1. 检查SSH密钥权限：`chmod 600 "SSH Secure Brain"`
2. 确认服务器IP地址正确
3. 检查防火墙是否阻止8000端口
4. 确认EverOS服务正在运行

### 如果API返回错误
1. 检查EverOS日志：`journalctl -u everos -f`
2. 验证API路径是否正确（/api/v2/memory/*）
3. 检查请求格式是否符合EverOS 1.2.3规范

## 6. 预期结果

成功的测试应该显示：
- ✓ 连接测试成功: 200
- ✓ 记忆检索成功: 找到 X 条结果
- ✓ 记忆添加成功: ID xxx
- ✓ 记忆固化成功
- ✓ 验证添加的记忆: 找到 1 条结果