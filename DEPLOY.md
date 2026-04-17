# Деплой на Yandex Cloud VM

## 1. Первый деплой

### На локальной машине — скопировать данные на сервер

```bash
# ChromaDB индекс
scp -r data/chroma ubuntu@111.88.159.72:~/app/data/

# Бэкап БД
scp data/cleaned.bak ubuntu@111.88.159.72:~/app/data/
```

### На сервере

```bash
# Установить Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu
newgrp docker

# Клонировать репозиторий
git clone <repo_url> ~/app
cd ~/app

# Создать .env
cp .env.example .env
nano .env  # заполнить все переменные

# Запустить
docker compose up -d --build
```

## 2. Обновление кода

```bash
cd ~/app
git pull
docker compose up -d --build
```

## 3. Проверка

```bash
# Статус контейнеров
docker compose ps

# Логи
docker compose logs -f api
docker compose logs -f user-bot
docker compose logs -f admin-bot

# Проверить API
curl http://localhost:8001/health
```

## 4. Открыть порт в Yandex Cloud

В консоли Yandex Cloud → VPC → Security Groups → добавить правило:
- Протокол: TCP
- Порт: 8001
- CIDR: 0.0.0.0/0
