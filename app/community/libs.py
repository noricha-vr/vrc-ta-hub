def get_join_type(join_str: str) -> str:
    if join_str.find('group/') != -1:
        return 'group'
    elif join_str.find('/user/') != -1:
        return 'user_page'
    elif join_str.find('vrch.at/') != -1:
        return 'world'
    else:
        return 'user_name'
