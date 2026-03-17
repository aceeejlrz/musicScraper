# YouTube Playlist Downloader

A lightweight web application for downloading YouTube playlists, with support for video and audio extraction.

## Prerequisites
- **Python 3.10+** (if running locally)
- **FFmpeg** (optional but highly recommended for merging video/audio streams and converting audio to mp3. If you run via Docker, FFmpeg is automatically included.)
- **Docker / Docker Desktop** (optional, for running in a container)

---

## 🚀 How to Run Locally (Windows)

1. Open a terminal in the project directory.
2. *(Optional but recommended)* Create and activate a virtual environment:
   ```cmd
   python -m venv .venv
   .venv\Scripts\activate
   ```
3. Install the dependencies:
   ```cmd
   pip install -r requirements.txt
   ```
4. Run the application using the batch file:
   ```cmd
   run.bat
   ```
   *Or run the Python script directly:*
   ```cmd
   python app.py
   ```
5. Open your browser and navigate to **`http://localhost:5050`**

---

## 🐳 How to Run with Docker

Running with Docker ensures that you have all dependencies (like FFmpeg) installed without cluttering your host system.

1. Ensure **Docker Desktop** is installed and running on your machine.
2. Open a **PowerShell** terminal in the project directory.
3. Build the Docker image:
   ```powershell
   docker build -t music-scraper .
   ```
4. Run the Docker container using this command:
   ```powershell
   docker run -p 5050:5050 -v "${PWD}/downloads:/app/downloads" music-scraper
   ```
   *Explanation of this command:*
   * `-p 5050:5050`: Connects the app inside Docker to `localhost:5050` on your computer.
   * `-v "${PWD}/downloads:/app/downloads"`: **(Crucial Step)** This connects a `downloads` folder on your computer to the `/app/downloads` folder inside the Docker container. This ensures that when the app downloads music, the files are actually visible on your Windows Desktop rather than getting trapped inside the container.

5. Open your browser and navigate to **`http://localhost:5050`**

### ⚠️ Important Notes when using Docker
* **Save Location:** The app will automatically detect it is running in Docker and set the "SAVE LOCATION" to `/app/downloads`. **Do not change this path!** Any files saved there will automatically appear in the `downloads` folder in your project directory on Windows.
* **Open Downloads Folder Button:** The "Open Downloads Folder" button on the website will not work when running in Docker, because the container does not have a graphical file explorer (like Windows Explorer). You will need to manually open the `downloads` folder in your `musicScraper` directory to view your files.
