import subprocess
import os
import sys


def get_venv_python():
    """Получаем путь к Python в виртуальном окружении"""
    if sys.platform == "win32":
        return os.path.join(".venv", "Scripts", "python.exe")
    return os.path.join(".venv", "bin", "python")


def run_scripts():
    # Получаем абсолютный путь к директории проекта
    project_dir = os.path.dirname(os.path.abspath(__file__))

    # Путь к Python в виртуальном окружении
    venv_python = os.path.join(project_dir, get_venv_python())

    # Проверяем существование виртуального окружения
    if not os.path.exists(venv_python):
        print(f"Ошибка: Виртуальное окружение не найдено по пути: {venv_python}")
        return

    # Пути к скриптам
    scripts = [
        os.path.join(project_dir, 'speech_processor.py'),
        os.path.join(project_dir, 'video_to_text.py')
    ]

    processes = []

    try:
        # Запускаем каждый скрипт с использованием Python из виртуального окружения
        for script in scripts:
            process = subprocess.Popen([venv_python, script])
            processes.append(process)
            print(f"Запущен скрипт: {os.path.basename(script)}")

        # Ожидаем завершения всех процессов
        for p in processes:
            p.wait()

    except KeyboardInterrupt:
        print("\nПолучен сигнал завершения. Останавливаем скрипты...")
        for p in processes:
            p.terminate()

        # Ждем корректного завершения процессов
        for p in processes:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

        print("Все скрипты остановлены")

    except Exception as e:
        print(f"Произошла ошибка: {e}")
        for p in processes:
            p.terminate()


if __name__ == "__main__":
    run_scripts()
