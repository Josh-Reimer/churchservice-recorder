
<p align="center">
  <img src="appicon.png" alt="App Icon" width="128" style="border-radius:23px;">
</p>
<h1 align="center">ByteWorship Recorder</h1>

A python program that can record listentochurch icecast streams to an mp3 file. You can install it using docker on a Debian linux machine.

## Goals
I'd like to extract Sunday morning announcements out of transcribed church services. Perhaps a flask webapp or mobile PWA to view them.
## build docker container

```
docker compose up -d --build
```
## Where to get the stream urls
You can find the urls here: [listentochurch.com](https://www.listentochurch.com/). You will need to look in the browser dev tools and guess the congregations names as I will not provide those names here.

This program will also work with other icecast streams such as internet radio.