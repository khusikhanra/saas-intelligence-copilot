FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
# Builds the DuckDB database on first start from data/raw (if mounted) or the bundled demo sample.
# Pass the key at runtime:  docker run -e OPENAI_API_KEY=... -p 8501:8501 saas-copilot
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
