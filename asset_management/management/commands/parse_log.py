"""
Django management command — 命令行解析日志并入库

用法: python manage.py parse_log <file_path> [file_path ...]
示例: python manage.py parse_log MTG-DUE-FRA15-VXLAN-6863-01.txt
"""

from django.core.management.base import BaseCommand, CommandError

from asset_management.parser import parse_log_text
from asset_management.views import UploadLogView
from django.contrib import messages

from django.test import RequestFactory


class Command(BaseCommand):
    help = "解析华为 CE 交换机日志文件并将数据入库"

    def add_arguments(self, parser):
        parser.add_argument("files", nargs="+", help="一个或多个日志文件路径")

    def handle(self, *args, **options):
        file_paths = options["files"]
        success_count = 0
        error_count = 0

        upload_view = UploadLogView()

        for file_path in file_paths:
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                upload_view._process_single_log(text, file_path)
                success_count += 1
                self.stdout.write(self.style.SUCCESS(f"  ✓ {file_path}"))
            except Exception as e:
                error_count += 1
                self.stderr.write(self.style.ERROR(f"  ✗ {file_path}: {e}"))

        if success_count:
            self.stdout.write(self.style.SUCCESS(f"\n成功解析 {success_count} 个文件。"))
        if error_count:
            self.stderr.write(self.style.ERROR(f"{error_count} 个文件解析失败。"))
