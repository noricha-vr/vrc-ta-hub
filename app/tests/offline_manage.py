"""Django初期化前から外向き通信を遮断して管理コマンドを実行する。"""

from website.tests.offline_runner import block_external_network


def main() -> None:
    """通信遮断を有効化してDjangoのmanage.py相当処理を実行する。"""
    with block_external_network():
        from manage import main as django_main

        django_main()


if __name__ == "__main__":
    main()
