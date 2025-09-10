# kirara2openApi
将kirara http api 转换成open api的python脚本

目文件结构
<TEXT>
kirara-proxy/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── app.py
└── logs/                   # 这个目录会自动创建


修改docker-compose.yaml中的 KIRARA_BASE_URL=你的kirara-agent地址

<BASH>
# 进入项目目录
cd kirara2openApi
 
# 构建并启动服务
docker-compose up -d

<BASH>
# 查看运行状态
docker-compose ps
 
# 查看日志
docker-compose logs -f kirara2openApi

<BASH>
# 停止服务
docker-compose down

<BASH>
# 重启服务
docker-compose restart
 
# 重新构建并启动
docker-compose up -d --build


<BASH>
# 检查健康状态
curl http://localhost:8081/health
