"""Base class cho model đọc/ghi YAML."""

from pathlib import Path
from typing import Dict, Optional, Any

import yaml
from pydantic import BaseModel


class YamlModel(BaseModel):
    """Base class cho YAML model.

    Cung cấp các method để đọc/ghi model từ/sang file YAML.
    """

    extra_fields: Optional[Dict[str, Any]] = None

    @classmethod
    def read_yaml(
        cls,
        file_path: Path,
        encoding: str = "utf-8"
    ) -> Dict:
        """Đọc file YAML và trả về dict.

        Raises:
            FileNotFoundError: Nếu file YAML không tồn tại
            yaml.YAMLError: Nếu file YAML bị lỗi định dạng
            IOError: Nếu có lỗi khi đọc file
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Không tìm thấy file YAML: {file_path}")

        try:
            with open(file_path, "r", encoding=encoding) as file:
                content = yaml.safe_load(file)
                return content if content is not None else {}
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Lỗi parse file YAML {file_path}: {e}")
        except IOError as e:
            raise IOError(f"Lỗi đọc file {file_path}: {e}")

    @classmethod
    def from_yaml_file(
        cls,
        file_path: Path
    ) -> "YamlModel":
        """Đọc file YAML và trả về instance YamlModel.

        Raises:
            FileNotFoundError: Nếu file YAML không tồn tại
            yaml.YAMLError: Nếu file YAML bị lỗi định dạng
            IOError: Nếu có lỗi khi đọc file
            ValidationError: Nếu nội dung YAML không khớp với schema của model
        """
        yaml_data = cls.read_yaml(file_path)
        try:
            return cls(**yaml_data)
        except Exception as e:
            raise ValueError(f"Lỗi tạo {cls.__name__} từ {file_path}: {e}")

    def to_yaml_file(
        self,
        file_path: Path,
        encoding: str = "utf-8"
    ) -> None:
        """Ghi instance YamlModel ra file YAML.

        Raises:
            IOError: Nếu có lỗi khi ghi file
            yaml.YAMLError: Nếu có lỗi khi serialize sang YAML
        """
        try:
            # Tạo thư mục cha nếu chưa tồn tại
            file_path.parent.mkdir(parents=True, exist_ok=True)

            with open(file_path, "w", encoding=encoding) as file:
                yaml.dump(self.model_dump(), file, default_flow_style=False)
        except IOError as e:
            raise IOError(f"Lỗi ghi file {file_path}: {e}")
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Lỗi serialize sang YAML: {e}")

    def to_yaml_string(self) -> str:
        """Export instance YamlModel sang chuỗi YAML.

        Returns:
            str: Biểu diễn YAML của model

        Raises:
            yaml.YAMLError: Nếu có lỗi khi serialize sang YAML
        """
        try:
            return yaml.dump(self.model_dump(), default_flow_style=False)
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Lỗi serialize sang chuỗi YAML: {e}")