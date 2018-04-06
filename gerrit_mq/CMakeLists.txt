set(gerrit_mq_py_files
    __init__.py
    __main__.py
    common.py
    daemon.py
    functions.py
    master.py
    orm.py
    webfront.py)

set(gerrit_mq_js_files
    templates/script.js.tpl)

add_custom_command(OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/gerrit_mq_format.stamp
                   COMMAND autopep8 -i ${gerrit_mq_py_files}
                   COMMAND python -Bm cmake_format -i CMakeLists.txt
                           doc/CMakeLists.txt
                   COMMAND touch
                           ${CMAKE_CURRENT_BINARY_DIR}/gerrit_mq_format.stamp
                   DEPENDS ${gerrit_mq_py_files}
                   WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})
add_custom_target(gerrit_mq_format
                  DEPENDS ${CMAKE_CURRENT_BINARY_DIR}/gerrit_mq_format.stamp)
add_dependencies(format gerrit_mq_format)

add_custom_command(OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/gerrit_mq_lint.stamp
                   COMMAND pylint ${gerrit_mq_py_files}
                   # COMMAND eslint ${gerrit_mq_js_files}
                   COMMAND touch
                           ${CMAKE_CURRENT_BINARY_DIR}/gerrit_mq_lint.stamp
                   DEPENDS ${gerrit_mq_py_files}
                   WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})
add_custom_target(gerrit_mq_lint
                  DEPENDS ${CMAKE_CURRENT_BINARY_DIR}/gerrit_mq_lint.stamp)

add_dependencies(lint gerrit_mq_lint)

add_subdirectory(doc)
