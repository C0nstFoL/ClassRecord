FROM python:3.12-slim

WORKDIR /app

# 使用国内 pip 镜像源加速依赖下载
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 前端静态文件需在构建镜像前预先执行 `npm run build`，
# 产物会输出到 ../frontend 的 outDir（backend/static），随本目录一起复制进镜像
EXPOSE 8002

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
