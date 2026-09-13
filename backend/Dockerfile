FROM python:3.12-slim

WORKDIR /app

# 使用国内 pip 镜像源加速依赖下载
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# sherpa-onnx CUDA 轮子链接了 ALSA（libasound.so.2）
RUN apt-get update && apt-get install -y --no-install-recommends libasound2 \
    && rm -rf /var/lib/apt/lists/*

# pip 安装的 CUDA 运行库（cublas/cudnn 等）不在系统默认搜索路径，
# ctranslate2 / onnxruntime CUDA EP 首次推理时才懒加载，需显式指定，
# 否则报 libcublas.so.12 not found
ENV NVIDIA_LIBS=/usr/local/lib/python3.12/site-packages/nvidia
ENV LD_LIBRARY_PATH=${NVIDIA_LIBS}/cublas/lib:${NVIDIA_LIBS}/cudnn/lib:${NVIDIA_LIBS}/cuda_runtime/lib:${NVIDIA_LIBS}/cuda_nvrtc/lib:${NVIDIA_LIBS}/cufft/lib:${NVIDIA_LIBS}/curand/lib:${NVIDIA_LIBS}/cusolver/lib:${NVIDIA_LIBS}/cusparse/lib:${NVIDIA_LIBS}/nvjitlink/lib:${LD_LIBRARY_PATH}

COPY . .

# 前端静态文件需在构建镜像前预先执行 `npm run build`，
# 产物会输出到 ../frontend 的 outDir（backend/static），随本目录一起复制进镜像
EXPOSE 8002

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
