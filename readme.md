
<p align="center">
  <img src="appicon.png" alt="App Icon" width="128" style="border-radius:23px;">
</p>
<h1 align="center">ByteWorship Recorder</h1>

A python program that can record listentochurch icecast streams to an mp3 file. You can install it using docker on a Debian linux machine.
The recordings and transcriptions are stored in ```recordings``` and ```transcriptions``` from the folder that the ```Dockerfile``` is in.

## Goals
I'd like to extract Sunday morning announcements out of transcribed church services. Perhaps a flask webapp or mobile PWA to view them.
Also, I would like to record the song numbers we sing and put it on a graph. I have a Christian hymnal pdf that I think I could do something with.
## build docker container

```
docker compose up -d --build
```

you can ssh into the container with this command:
```
docker exec -it church-recorder bash
```
and view the webserver container like so:
```
docker exec -it church-webserver bash
```

## View the web interface for recordings and transcriptions
By default, the flask web ui is at [http://0.0.0.0:5000](http://0.0.0.0:5000)
The default user name is ```admin``` and the default password is ```42```. There is not a way to change them right now except by editing the values in ```webserver.py```

## Multi-stream recording
I am working on building a mulit-stream recording function which will use the config/streams.yml for listing the stream urls and timezones. 
Here is an example:
```
streams:
  - name: stream1
    url: https://example.com/stream1.mp3
    status_url: https://example.com/api/stream1/status
    timezone: America/Mexico_City
    sunday_morning_service_time: "10:00"
    sunday_evening_service_time: "18:00"
  - name: stream2
    url: https://example.com/stream2.mp3
    status_url: https://example.com/api/stream2/status
    timezone: America/Denver
    sunday_morning_service_time: "10:00"
    sunday_evening_service_time: "18:00"
```


## APIs used and keys required
This app makes use of telegram for recording updates, and OpenAI whisper for text transcribing. The app will run without the text transcription but you will need a telegram bot api key. 

All of the secrets in this program will need to be stored in a .env file. You can create one like this: 

```
cat <<EOF > .env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_user_id_here
OPENAI_API_KEY=your_openai_api_key_here
STREAM_URL=your_icecast_stream_url_here
STREAM_STATUS_URL=your_stream_status_url_here
TIMEZONE=your_timezone_here
LOG_LEVEL="INFO"
ADMIN_PASS_HASH="HASH HERE"
EOF
```


## Where to get the stream urls
You can find the urls here: [listentochurch.com](https://www.listentochurch.com/). You will need to look in the browser dev tools and guess the congregations names as I will not provide those names here.

This program will also work with other icecast streams such as internet radio.
