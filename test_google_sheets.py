#!/usr/bin/env python3
"""
Скрипт для тестирования подключения к Google Sheets
"""

import os
import sys

# Добавляем текущую директорию в путь для импорта
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from bot import test_google_sheets_connection, load_categories, open_sheet

def main():
    print("🔍 Тестирование подключения к Google Sheets...")
    print("=" * 50)
    
    # Проверяем переменные окружения
    print("📋 Проверка переменных окружения:")
    creds_path = os.getenv("GOOGLE_CREDS_PATH")
    sheet_name = os.getenv("SHEET_NAME")
    
    if creds_path:
        print(f"✅ GOOGLE_CREDS_PATH: {creds_path}")
        if os.path.exists(creds_path):
            print("✅ Файл credentials найден")
        else:
            print("❌ Файл credentials не найден")
    else:
        print("❌ GOOGLE_CREDS_PATH не установлена")
    
    if sheet_name:
        print(f"✅ SHEET_NAME: {sheet_name}")
    else:
        print("❌ SHEET_NAME не установлена")
    
    print("\n🔗 Тестирование подключения:")
    
    # Тестируем подключение
    if test_google_sheets_connection():
        print("✅ Подключение к Google Sheets успешно!")
        
        # Пытаемся загрузить категории
        print("\n📋 Загрузка категорий:")
        try:
            categories = load_categories()
            if categories:
                print(f"✅ Загружено {len(categories)} категорий:")
                for i, cat in enumerate(categories, 1):
                    print(f"  {i}. {cat}")
            else:
                print("⚠️  Категории не найдены в листе Config")
        except Exception as e:
            print(f"❌ Ошибка при загрузке категорий: {e}")
        
        # Пытаемся открыть основной лист данных
        print("\n📊 Тестирование основного листа данных:")
        try:
            data_sheet = open_sheet("Data")
            print("✅ Лист Data доступен")
            
            # Пытаемся получить несколько строк
            rows = data_sheet.get_all_values()
            if rows:
                print(f"✅ Найдено {len(rows)} строк данных")
                if len(rows) > 1:
                    print("📋 Заголовки столбцов:")
                    headers = rows[0]
                    for i, header in enumerate(headers):
                        print(f"  {i+1}. {header}")
            else:
                print("⚠️  Лист Data пуст")
        except Exception as e:
            print(f"❌ Ошибка при доступе к листу Data: {e}")
    
    else:
        print("❌ Не удалось подключиться к Google Sheets")
        print("\n💡 Возможные причины:")
        print("1. Неправильный путь к файлу credentials")
        print("2. Неправильное название таблицы")
        print("3. Проблемы с правами доступа")
        print("4. Проблемы с интернет-соединением")

if __name__ == "__main__":
    main()
