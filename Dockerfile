FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium
COPY catchgpu ./catchgpu
ENV CATCHGPU_HOST=0.0.0.0 CATCHGPU_DATA_DIR=/data
EXPOSE 8765
CMD ["python", "-m", "catchgpu"]

