# kirara2openApi
将kirara http api 转换成open api

将脚本下载到服务器

文件结构

kirara-kirara2openApi/

├── Dockerfile

├── docker-compose.yml

├── requirements.txt

├── app.py

└── logs/
// logs这个目录会自动创建



# 前提
修改docker-compose.yml中的 KIRARA_BASE_URL=你的kirara-agent地址
再kirara中创建http api，将apikey写入docker-compose.yml中的 KIRARA_API_KEY=你的key

# 进入项目目录
cd kirara2openApi
 
# 构建并启动服务
docker-compose up -d


 
# 查看日志
docker-compose logs -f kirara2openApi


# 停止服务
docker-compose down


# 重启服务
docker-compose restart
 
# 重新构建并启动
docker-compose up -d --build



# 检查健康状态
curl http://localhost:8081/health
