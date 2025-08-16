from mutagen.mp3 import MP3

def get_audio_length(file_path):
    """
    Get the length of an audio file in seconds.
    
    :param file_path: Path to the audio file.
    :return: Length of the audio file in seconds.
    """
    audio = MP3(file_path)
    return int(audio.info.length)

# function to convert the information into 
# some readable format
def audio_duration(length):
    hours = length // 3600  # calculate in hours
    length %= 3600
    mins = length // 60  # calculate in minutes
    length %= 60
    seconds = length  # calculate in seconds

    return hours, mins, seconds  # returns the duration