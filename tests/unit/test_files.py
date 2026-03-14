from app.utils.files import file_extension_from_path


class TestFileExtensionFromPath:
    def test_simple_py(self):
        assert file_extension_from_path("src/main.py") == "py"

    def test_nested_tsx(self):
        assert file_extension_from_path("frontend/components/App.tsx") == "tsx"

    def test_dotfile(self):
        assert file_extension_from_path(".gitignore") == "gitignore"

    def test_no_extension(self):
        assert file_extension_from_path("Makefile") is None

    def test_multiple_dots(self):
        assert file_extension_from_path("archive.tar.gz") == "gz"

    def test_uppercase(self):
        assert file_extension_from_path("README.MD") == "md"

    def test_php(self):
        assert file_extension_from_path("app/Http/Controller.php") == "php"

    def test_java(self):
        assert file_extension_from_path("src/main/java/App.java") == "java"

    def test_empty_string(self):
        assert file_extension_from_path("") is None
