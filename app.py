from flask import Flask, render_template, request, jsonify, send_file
import subprocess
import os
import json
import re
from pathlib import Path
import threading
import time
from queue import Queue
from datetime import datetime

app = Flask(__name__)

# 下載任務狀態追蹤
download_tasks = {}
# 下載隊列
download_queue = Queue()
# 隊列處理器運行標誌
queue_processor_running = False

class DownloadTask:
    def __init__(self, task_id, url, download_type, quality, max_retries=0, compatible_mode=False):
        self.task_id = task_id
        self.url = url
        self.download_type = download_type
        self.quality = quality
        self.status = "等待中"
        self.progress = 0
        self.filename = ""
        self.error = None
        self.added_time = datetime.now().strftime("%H:%M:%S")
        self.process = None  # 用於追蹤子進程
        self.cancelled = False  # 取消旗標
        self.max_retries = max_retries  # 重試上限，0 表示不限制
        self.compatible_mode = compatible_mode
        
def extract_progress(line):
    """從 yt-dlp 輸出中提取下載進度"""
    # 匹配形如 "[download]  45.2% of 10.5MiB at 1.2MiB/s ETA 00:05"
    match = re.search(r'\[download\]\s+(\d+\.?\d*)%', line)
    if match:
        return float(match.group(1))
    return None

def process_download(task):
    """處理單一下載任務"""
    
    try:
        # 準備下載目錄
        download_dir = Path(__file__).parent / "downloads"
        download_dir.mkdir(exist_ok=True)
        
        # 構建 yt-dlp 命令
        yt_dlp_path = Path(__file__).parent / "yt-dlp.exe"
        
        if task.download_type == "video":
            # 影片下載 - 使用更寬鬆的格式選擇
            if task.compatible_mode:
                # 汽車相容模式：強制 H.264 (AVC) + 任何音訊
                if task.quality == "best":
                    format_str = "bv*[vcodec^=avc]+ba/bv*[vcodec^=avc]/b[ext=mp4]/b"
                elif task.quality == "1080p":
                    format_str = "bv*[height<=1080][vcodec^=avc]+ba/bv*[height<=1080][vcodec^=avc]/b[height<=1080][ext=mp4]/b"
                elif task.quality == "720p":
                    format_str = "bv*[height<=720][vcodec^=avc]+ba/bv*[height<=720][vcodec^=avc]/b[height<=720][ext=mp4]/b"
                else:  # 480p
                    format_str = "bv*[height<=480][vcodec^=avc]+ba/bv*[height<=480][vcodec^=avc]/b[height<=480][ext=mp4]/b"
            else:
                # 原本的邏輯 (優先最佳品質，不限編碼)
                if task.quality == "best":
                    # 優先 mp4，但接受任何最佳格式
                    format_str = "bv*+ba/b"
                elif task.quality == "1080p":
                    # 1080p 或更低，有多層 fallback
                    format_str = "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
                elif task.quality == "720p":
                    format_str = "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b"
                else:  # 480p
                    format_str = "bv*[height<=480]+ba/b[height<=480]/bv*+ba/b"
            
            cmd = [
                str(yt_dlp_path),
                "-f", format_str,
                "--merge-output-format", "mp4",
                "-o", str(download_dir / "%(title)s.%(ext)s"),
                "--no-warnings",
                "--ignore-errors",
                task.url
            ]
        else:
            # 音訊下載
            cmd = [
                str(yt_dlp_path),
                "-x",  # 提取音訊
                "--audio-format", "mp3",
                "--audio-quality", "0",
                "-o", str(download_dir / "%(title)s.%(ext)s"),
                "--no-warnings",
                task.url
            ]
            
            # 如果有指定品質
            if task.quality != "best":
                cmd.insert(2, "--postprocessor-args")
                cmd.insert(3, f"ffmpeg:-b:a {task.quality}k")
        
        task.status = "下載中"
        
        # Windows 編碼修正
        import os
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        
        # 執行下載
        task.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace',
            env=env
        )
        
        # 讀取輸出以追蹤進度
        retry_count = 0
        max_retries = task.max_retries if hasattr(task, 'max_retries') and task.max_retries > 0 else 0
        
        for line in task.process.stdout:
            # 檢查是否被取消
            if task.cancelled:
                task.process.terminate()
                task.status = "已取消"
                return
            
            try:
                print(line.strip())  # 除錯用
            except UnicodeEncodeError:
                print(line.strip().encode('ascii', errors='replace').decode('ascii'))
            
            # 檢查錯誤重試（僅在設定了上限時）
            if max_retries > 0 and "Got error" in line and "Retrying" in line:
                retry_count += 1
                print(f"[警告] 重試次數: {retry_count}/{max_retries}")
                
                if retry_count >= max_retries:
                    print(f"[錯誤] 重試次數超過 {max_retries} 次，終止下載")
                    task.process.terminate()
                    task.status = "失敗"
                    task.error = f"下載失敗：錯誤重試次數超過 {max_retries} 次（可能是網路問題或影片有限制）"
                    return
            
            # 更新進度
            progress = extract_progress(line)
            if progress:
                task.progress = progress
        
        task.process.wait()
        
        # 再次檢查是否被取消
        if task.cancelled:
            task.status = "已取消"
            return
        
        if task.process.returncode == 0:
            task.status = "完成"
            task.progress = 100
            
            # 從檔案系統找出最新下載的檔案
            try:
                files = list(download_dir.glob("*"))
                if files:
                    latest_file = max(files, key=lambda f: f.stat().st_mtime)
                    task.filename = latest_file.name
            except:
                task.filename = "下載完成"
        else:
            task.status = "失敗"
            task.error = "下載失敗，請檢查 URL 是否正確"
            
    except Exception as e:
        task.status = "失敗"
        task.error = str(e)

def queue_processor():
    """隊列處理器 - 持續從隊列中取出任務並執行"""
    global queue_processor_running
    queue_processor_running = True
    
    while queue_processor_running:
        try:
            # 從隊列取出任務（最多等待1秒）
            task = download_queue.get(timeout=1)
            
            # 處理任務
            task.status = "下載中"
            process_download(task)
            
            # 標記任務完成
            download_queue.task_done()
        except:
            # 隊列為空或其他錯誤，繼續循環
            continue

def start_queue_processor():
    """啟動隊列處理器"""
    global queue_processor_running
    if not queue_processor_running:
        thread = threading.Thread(target=queue_processor, daemon=True)
        thread.start()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    data = request.json
    url = data.get('url', '').strip()
    download_type = data.get('type', 'video')  # video 或 audio
    quality = data.get('quality', 'best')
    max_retries = data.get('max_retries', 0)  # 預設 0 = 不限制
    compatible_mode = data.get('compatible_mode', False)
    
    if not url:
        return jsonify({'success': False, 'error': '請輸入 YouTube URL'})
    
    # 基本的 URL 驗證
    if not ('youtube.com' in url or 'youtu.be' in url):
        return jsonify({'success': False, 'error': '請輸入有效的 YouTube URL'})
    
    # 啟動隊列處理器（如果還沒啟動）
    start_queue_processor()
    
    # 創建下載任務
    task_id = str(int(time.time() * 1000))
    task = DownloadTask(task_id, url, download_type, quality, max_retries, compatible_mode)
    download_tasks[task_id] = task
    
    # 加入隊列
    download_queue.put(task)
    
    return jsonify({
        'success': True,
        'task_id': task_id
    })

@app.route('/status/<task_id>')
def get_status(task_id):
    task = download_tasks.get(task_id)
    if not task:
        return jsonify({'error': '任務不存在'})
    
    return jsonify({
        'status': task.status,
        'progress': task.progress,
        'filename': task.filename,
        'error': task.error
    })

@app.route('/queue')
def get_queue():
    """取得所有任務狀態"""
    tasks = []
    for task_id, task in download_tasks.items():
        tasks.append({
            'task_id': task.task_id,
            'url': task.url,
            'type': task.download_type,
            'quality': task.quality,
            'status': task.status,
            'progress': task.progress,
            'filename': task.filename,
            'error': task.error,
            'added_time': task.added_time
        })
    
    # 按照添加時間排序（最新在前）
    tasks.sort(key=lambda x: x['task_id'], reverse=True)
    
    return jsonify({
        'tasks': tasks,
        'queue_size': download_queue.qsize()
    })

@app.route('/cancel/<task_id>', methods=['POST'])
def cancel_task(task_id):
    """取消指定的下載任務"""
    task = download_tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任務不存在'})
    
    # 如果任務還在等待中或下載中
    if task.status in ['等待中', '下載中']:
        task.cancelled = True
        
        # 如果有正在執行的進程，終止它
        if task.process:
            try:
                task.process.terminate()
                task.process.wait(timeout=3)
            except:
                # 如果 terminate 失敗，強制 kill
                try:
                    task.process.kill()
                except:
                    pass
        
        task.status = "已取消"
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': '只能取消等待中或下載中的任務'})

@app.route('/retry/<task_id>', methods=['POST'])
def retry_task(task_id):
    """重試失敗的下載任務"""
    task = download_tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任務不存在'})
    
    # 只能重試失敗的任務
    if task.status != '失敗':
        return jsonify({'success': False, 'error': '只能重試失敗的任務'})
    
    # 重置任務狀態
    task.status = '等待中'
    task.progress = 0
    task.error = None
    task.cancelled = False
    task.process = None
    
    # 重新加入隊列
    download_queue.put(task)
    
    # 啟動隊列處理器（如果還沒啟動）
    start_queue_processor()
    
    return jsonify({'success': True})

@app.route('/downloads')
def list_downloads():
    """列出所有已下載的檔案"""
    download_dir = Path(__file__).parent / "downloads"
    if not download_dir.exists():
        return jsonify({'files': []})
    
    files = []
    for file in download_dir.iterdir():
        if file.is_file():
            files.append({
                'name': file.name,
                'size': file.stat().st_size,
                'modified': file.stat().st_mtime
            })
    
    # 按修改時間排序
    files.sort(key=lambda x: x['modified'], reverse=True)
    return jsonify({'files': files})

@app.route('/clear-history', methods=['POST'])
def clear_history():
    """清除下載任務歷史（僅清除完成和失敗的任務）"""
    global download_tasks
    
    # 只保留正在進行中和等待中的任務
    active_tasks = {
        task_id: task for task_id, task in download_tasks.items()
        if task.status in ['等待中', '下載中']
    }
    
    download_tasks = active_tasks
    
    return jsonify({'success': True, 'message': '已清除完成和失敗的任務'})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
