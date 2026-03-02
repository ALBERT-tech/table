import os
import zipfile
import tempfile
import time
import logging
from pathlib import Path
from flask import Flask, request, render_template_string, send_file, url_for
from werkzeug.utils import secure_filename
import pandas as pd
from docling.document_converter import DocumentConverter

# --- Настройки ---
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'pptx', 'xlsx', 'png', 'jpg', 'jpeg', 'tiff', 'bmp'} # Добавьте нужные
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB
TEMP_FILE_TTL = 600  # 10 минут

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
logging.basicConfig(level=logging.INFO)

# Хранилище для временных данных: {file_id: {'zip_path': Path, 'expires': time.time()}}
temp_storage = {}

# --- HTML-шаблон для ответа (как в вашем плане) ---
RESULT_TEMPLATE = """
<!DOCTYPE html>
<div class="container">
    {% if error %}
        <div class="alert alert-danger">{{ error }}</div>
    {% else %}
        <div class="alert alert-success">Найдено таблиц: {{ tables_count }}</div>
        
        {% if tables_count > 0 %}
            <a href="{{ url_for('download', file_id=file_id) }}" class="btn btn-success mb-3">Скачать все таблицы (ZIP)</a>
            
            {% for table_html in previews %}
                <div class="card mb-4">
                    <div class="card-header">Таблица {{ loop.index }}</div>
                    <div class="card-body table-responsive">
                        {{ table_html | safe }}
                    </div>
                </div>
            {% endfor %}
        {% else %}
            <p>Таблицы в документе не обнаружены.</p>
        {% endif %}
    {% endif %}
</div>
"""

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def cleanup_old_files():
    """Удаляет устаревшие временные файлы."""
    now = time.time()
    expired = [fid for fid, data in temp_storage.items() if data['expires'] < now]
    for fid in expired:
        try:
            Path(temp_storage[fid]['zip_path']).unlink(missing_ok=True)
            del temp_storage[fid]
            app.logger.info(f"Удален временный файл {fid}")
        except Exception as e:
            app.logger.error(f"Ошибка удаления {fid}: {e}")

@app.route('/upload', methods=['POST'])
def upload_file():
    cleanup_old_files()
    
    # 1. Валидация файла
    if 'file' not in request.files:
        return render_template_string(RESULT_TEMPLATE, error="Файл не найден")
    
    file = request.files['file']
    if file.filename == '':
        return render_template_string(RESULT_TEMPLATE, error="Файл не выбран")
    
    if not allowed_file(file.filename):
        return render_template_string(RESULT_TEMPLATE, 
                                      error=f"Неподдерживаемый формат. Разрешенные: {', '.join(ALLOWED_EXTENSIONS)}")
    
    # 2. Сохраняем во временную папку
    filename = secure_filename(file.filename)
    with tempfile.TemporaryDirectory() as tmpdirname:
        input_path = Path(tmpdirname) / filename
        file.save(input_path)
        
        # 3. Обработка через Docling (как в примере!)
        try:
            doc_converter = DocumentConverter()
            # Важно: Для PDF, чтобы распознавались таблицы, убедитесь, что включены нужные опции
            # По умолчанию table structure detection включен для PDF.
            conv_res = doc_converter.convert(input_path)
            
            tables = conv_res.document.tables
            if not tables:
                return render_template_string(RESULT_TEMPLATE, tables_count=0, previews=[])
            
            # Папка для CSV внутри временной директории
            csv_dir = Path(tmpdirname) / "csvs"
            csv_dir.mkdir()
            
            previews = []
            doc_filename = conv_res.input.file.stem
            
            # 4. Цикл по таблицам (как в примере)
            for table_ix, table in enumerate(tables):
                # Экспорт в DataFrame (основной метод из примера)
                table_df: pd.DataFrame = table.export_to_dataframe(doc=conv_res.document)
                
                # Сохраняем CSV
                csv_path = csv_dir / f"{doc_filename}-table-{table_ix+1}.csv"
                table_df.to_csv(csv_path, index=False)
                
                # Создаем превью (первые 10 строк)
                preview_html = table_df.head(10).to_html(classes="table table-sm table-striped")
                previews.append(preview_html)
            
            # 5. Создаем ZIP-архив
            zip_filename = f"{doc_filename}_tables.zip"
            zip_path = Path(tmpdirname) / zip_filename
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for csv_file in csv_dir.glob("*.csv"):
                    zipf.write(csv_file, arcname=csv_file.name)
            
            # 6. Сохраняем ссылку на ZIP во временном хранилище
            file_id = os.urandom(8).hex()
            temp_storage[file_id] = {
                'zip_path': str(zip_path),
                'expires': time.time() + TEMP_FILE_TTL
            }
            
            return render_template_string(RESULT_TEMPLATE, 
                                         tables_count=len(tables),
                                         previews=previews,
                                         file_id=file_id)
        
        except Exception as e:
            app.logger.error(f"Ошибка обработки: {e}", exc_info=True)
            return render_template_string(RESULT_TEMPLATE, 
                                         error="Не удалось обработать документ. Возможно, файл поврежден или имеет неподдерживаемую структуру.")

@app.route('/download/<file_id>')
def download(file_id):
    if file_id not in temp_storage:
        return "Файл не найден или срок его хранения истек.", 404
    
    data = temp_storage[file_id]
    zip_path = Path(data['zip_path'])
    
    if not zip_path.exists():
        return "Файл был удален.", 404
    
    return send_file(zip_path, as_attachment=True, download_name=zip_path.name)

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
