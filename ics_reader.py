#!/usr/bin/env python3
import os
import time
from datetime import datetime, timedelta
from icalendar import Calendar, Event


def get_recently_modified_files(directory_path, minutes=15):
    """
    Find files modified within the specified number of minutes in the given directory.

    Args:
        directory_path (str): Path to the directory to search
        minutes (int): Number of minutes to look back (default: 15)

    Returns:
        list: List of tuples containing (filename, modification_time)
    """
    # Get current time
    current_time = time.time()
    # Calculate the timestamp from 15 minutes ago
    time_threshold = current_time - (minutes * 60)

    recent_files = []

    print(directory_path)
    # Walk through directory
    for root, dirs, files in os.walk(directory_path):
        for file in files:
            file_path = os.path.join(root, file)
            try:
                # Get file's last modification time
                mtime = os.path.getmtime(file_path)

                # Check if file was modified within the last 15 minutes
                if mtime > time_threshold:
                    # Convert timestamp to readable datetime
                    mod_time = datetime.fromtimestamp(mtime)
                    recent_files.append((file_path, mod_time))
            except OSError as e:
                print(f"Error accessing {file_path}: {e}")

    return sorted(recent_files, key=lambda x: x[1], reverse=True)


def read_ics_file(file_path):
    with open(file_path, "rb") as file:
        cal = Calendar.from_ical(file.read())

    for component in cal.walk("VEVENT"):
        if component.get("ATTACH"):
            # exist event in notion
            print(f"Attachment found: {component.get('ATTACH')}")
        else:
            # new event need to sync to notion
            print(f"No attachment found in {file_path}")

    with open(file_path, "wb") as output:
        output.write(cal.to_ical())


# Example usage
if __name__ == "__main__":
    # Replace with your directory path
    directory = r"/home/mwei/.calendars/vmin6810@gmail.com"

    recent_files = get_recently_modified_files(directory)

    if recent_files:
        print(f"Files modified within the last 15 minutes:")
        for file_path, mod_time in recent_files:
            print(f"{file_path} - Last modified: {mod_time}")
    else:
        print("No files were modified within the last 15 minutes.")
