FROM python:3.10.10-slim-buster

WORKDIR /app

RUN apt update -y && apt-get install -y python3-dev build-essential
RUN groupadd -r keeper && useradd -r -g keeper keeper

COPY requirements.txt .
COPY install.sh .
RUN ./install.sh

COPY poly_market_maker poly_market_maker
COPY bin bin
COPY logging.yaml .
COPY logs logs
RUN chmod 777 logs
RUN chown -R nonroot:nonroot /app/logs

WORKDIR /app/bin
USER root