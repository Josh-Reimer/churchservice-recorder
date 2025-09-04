
<p align="center">
  <img src="appicon.png" alt="App Icon" width="128" style="border-radius:23px;">
</p>
<h1 align="center">ByteWorship Recorder</h1>

A python program that can record listentochurch icecast streams to an mp3 file. You can install it using docker on a Debian linux machine.

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

## Start the web interface for viewing recordings

The flask web interface is not enabled in the docker container yet, so if you install the project without building the docker container, you can run the command ```python3 webserver.py``` in the same folder as the rest of the code. Note that you may have to run ```python3 -m venv venv && source venv/bin/activate``` first to enable a virtual environment.


## APIs used and keys required
This app makes use of telegram for recording updates, and OpenAI whisper for text transcribing. The app will run without the text transcription but you will need a telegram bot api key. 

All of the secrets in this program will need to be stored in a .env file. You can create one like this: 

```
cat <<EOF > .env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
OPENAI_API_KEY=your_openai_api_key_here
STREAM_URL=your_icecast_stream_url_here
STREAM_STATUS_URL=your_stream_status_url_here
TIMEZONE=your_timezone_here
EOF
```


## Where to get the stream urls
You can find the urls here: [listentochurch.com](https://www.listentochurch.com/). You will need to look in the browser dev tools and guess the congregations names as I will not provide those names here.

This program will also work with other icecast streams such as internet radio.
