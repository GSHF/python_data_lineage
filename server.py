from flask import Flask, request, jsonify, send_from_directory
import os
import tempfile
import json
import sys
import re
import traceback
from io import StringIO
import contextlib
import jpype
import atexit
import threading

app = Flask(__name__)
jvm_lock = threading.Lock()

def init_jvm():
    """初始化JVM"""
    if not jpype.isJVMStarted():
        jar = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jar", "gudusoft.gsqlparser-2.8.5.8.jar")
        jvm = jpype.getDefaultJVMPath()
        jpype.startJVM(jvm, "-ea", "-Djava.class.path=" + jar)

def cleanup_jvm():
    """清理JVM"""
    if jpype.isJVMStarted():
        jpype.shutdownJVM()

@app.route('/')
def index():
    return send_from_directory('widget', 'index.html')

@app.route('/widget/<path:filename>')
def serve_static(filename):
    return send_from_directory('widget', filename)

@app.route('/analyze', methods=['POST'])
def analyze():
    temp_path = None
    try:
        data = request.json
        db_type = data.get('dbType', 'oracle')
        sql = data.get('sql')
        analysis_type = data.get('type', 'lineage')  # 'lineage' or 'er'
        
        if not sql:
            return jsonify({'error': 'SQL is required'}), 400
            
        # 进行SQL语法检查
        with jvm_lock:
            if not jpype.isJVMStarted():
                init_jvm()
                
            # 获取需要的Java类
            EDbVendor = jpype.JClass("gudusoft.gsqlparser.EDbVendor")
            TGSqlParser = jpype.JClass("gudusoft.gsqlparser.TGSqlParser")
            
            # 获取数据库类型
            db_vendor_map = {
                'oracle': EDbVendor.dbvoracle,
                'mysql': EDbVendor.dbvmysql,
                'postgresql': EDbVendor.dbvpostgresql,
                'sqlserver': EDbVendor.dbvmssql,
                'hive': EDbVendor.dbvhive,
                'snowflake': EDbVendor.dbvsnowflake,
                'db2': EDbVendor.dbvdb2,
                'greenplum': EDbVendor.dbvgreenplum,
                'informix': EDbVendor.dbvinformix,
                'netezza': EDbVendor.dbvnetezza,
                'redshift': EDbVendor.dbvredshift,
                'sybase': EDbVendor.dbvsybase,
                'teradata': EDbVendor.dbvteradata
            }
            vendor = db_vendor_map.get(db_type.lower(), EDbVendor.dbvoracle)
            
            # 创建SQL解析器
            parser = TGSqlParser(vendor)
            parser.sqltext = sql
            
            # 解析SQL
            result = parser.parse()
            if result != 0:
                # 获取错误信息
                error_message = str(parser.getErrormessage())
                errors = []
                
                # 将错误信息按行分割
                for line in error_message.splitlines():
                    if line:
                        error_info = {
                            'message': '',
                            'line': 1,
                            'column': 1,
                            'length': 1,
                            'type': 'error'
                        }
                        
                        # 解析不同类型的错误
                        if 'tokenlize' in line:
                            # 处理词法错误
                            match = re.search(r'tokenlize\((\d+)\).*near:\s*([^(]+)\((\d+),(\d+)', line)
                            if match:
                                error_code, text, line_no, col_no = match.groups()
                                error_info.update({
                                    'message': f"发现非法符号 '{text.strip()}'",
                                    'line': int(line_no),
                                    'column': int(col_no),
                                    'length': len(text.strip()),
                                    'type': 'lexical'
                                })
                            else:
                                error_info['message'] = f"词法错误: {line}"
                        elif 'syntax error' in line:
                            # 处理语法错误
                            match = re.search(r'syntax error,\s*state:(\d+)\((\d+)\).*near:\s*([^(]+)\((\d+),(\d+)', line)
                            if match:
                                state, error_code, text, line_no, col_no = match.groups()
                                # 根据错误状态码提供更详细的错误信息
                                error_details = {
                                    '941': '需要SELECT关键字',
                                    '942': '需要FROM关键字',
                                    '943': '表名不合法或缺失',
                                    '944': '列名不合法或缺失',
                                    '945': '需要WHERE关键字',
                                    '946': '条件表达式不完整或语法错误',
                                    '947': '需要GROUP BY关键字',
                                    '948': '需要ORDER BY关键字',
                                    '949': '需要HAVING关键字',
                                    '950': '子查询语法错误',
                                    '951': '关键字顺序错误',
                                    '952': '缺少必要的括号',
                                    '953': '括号不匹配',
                                    '954': '运算符使用错误',
                                    '955': '函数语法错误',
                                    '956': '表达式语法错误',
                                    '957': '列表语法错误',
                                    '958': '值列表语法错误',
                                    '959': 'JOIN语法错误',
                                    '960': '条件语法错误'
                                }.get(state, '语法错误')
                                
                                error_info.update({
                                    'message': f"{error_details}, 错误发生在 '{text.strip()}' 附近",
                                    'line': int(line_no),
                                    'column': int(col_no),
                                    'length': len(text.strip()),
                                    'type': 'syntax',
                                    'code': state
                                })
                            else:
                                error_info['message'] = f"语法错误: {line}"
                        elif 'no_root_node' in line:
                            # 处理没有根节点的错误
                            error_info['message'] = "SQL语句不完整或缺少必要的关键字"
                        else:
                            # 其他错误
                            error_info['message'] = f"语法错误: {line}"
                        
                        errors.append(error_info)
                
                if not errors:
                    errors.append({
                        'message': error_message,
                        'line': 1,
                        'column': 1,
                        'length': 1,
                        'type': 'error'
                    })
                    
                return jsonify({'errors': errors}), 400
            
        # 创建临时SQL文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False, encoding='utf-8') as temp:
            temp.write(sql)
            temp_path = temp.name
            
        # 使用线程锁确保JVM操作的线程安全
        with jvm_lock:
            # 确保JVM已启动
            if not jpype.isJVMStarted():
                init_jvm()
            
            # 捕获输出
            output = StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                # 获取需要的Java类
                EDbVendor = jpype.JClass("gudusoft.gsqlparser.EDbVendor")
                DataFlowAnalyzer = jpype.JClass("gudusoft.gsqlparser.dlineage.DataFlowAnalyzer")
                DataFlowGraphGenerator = jpype.JClass("gudusoft.gsqlparser.dlineage.graph.DataFlowGraphGenerator")
                File = jpype.JClass("java.io.File")
                
                # 创建Java File对象
                sql_file = File(temp_path)
                
                # 获取数据库类型
                db_vendor_map = {
                    'oracle': EDbVendor.dbvoracle,
                    'mysql': EDbVendor.dbvmysql,
                    'postgresql': EDbVendor.dbvpostgresql,
                    'sqlserver': EDbVendor.dbvmssql,
                    'hive': EDbVendor.dbvhive,
                    'snowflake': EDbVendor.dbvsnowflake,
                    'db2': EDbVendor.dbvdb2,
                    'greenplum': EDbVendor.dbvgreenplum,
                    'informix': EDbVendor.dbvinformix,
                    'netezza': EDbVendor.dbvnetezza,
                    'redshift': EDbVendor.dbvredshift,
                    'sybase': EDbVendor.dbvsybase,
                    'teradata': EDbVendor.dbvteradata
                }
                vendor = db_vendor_map.get(db_type.lower(), EDbVendor.dbvoracle)
                
                # 创建分析器实例并设置选项
                instance = DataFlowAnalyzer(sql_file, vendor, False)  # 设置simple=False以显示详细信息
                
                # 设置分析选项
                instance.setShowJoin(True)  # 显示JOIN关系
                instance.setShowCallRelation(True)  # 显示函数调用关系
                instance.setIgnoreTemporaryTable(False)  # 不忽略临时表
                instance.setShowConstantTable(True)  # 显示常量表
                instance.setIgnoreRecordSet(False)  # 不忽略记录集
                instance.setSimpleShowFunction(False)  # 显示完整的函数信息
                
                # 生成数据流
                instance.generateDataFlow()
                dataflow = instance.getDataFlow()
                
                # 生成图形数据
                generator = DataFlowGraphGenerator()
                
                if analysis_type == 'er':
                    # 生成ER图
                    instance.getOption().setShowERDiagram(True)
                    result = generator.genERGraph(vendor, dataflow)
                else:
                    # 生成血缘图
                    result = generator.genDlineageGraph(vendor, True, dataflow)  # 设置detailed=True
                
                # 保存结果
                json_path = os.path.join('widget', 'json', 'lineageGraph.json')
                with open(json_path, 'w', encoding='utf-8') as f:
                    f.write(str(result))
                
            # 检查输出中是否有错误信息
            output_text = output.getvalue()
            if "error" in output_text.lower() or "exception" in output_text.lower():
                return jsonify({'error': output_text}), 500
                
            # 读取生成的JSON文件
            with open(json_path, 'r', encoding='utf-8') as f:
                result = json.load(f)
                
            return jsonify(result)
            
    except Exception as e:
        error_msg = f"Error: {str(e)}\nTraceback:\n{traceback.format_exc()}"
        return jsonify({'error': error_msg}), 500
        
    finally:
        # 清理临时文件
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except:
                pass

@app.route('/graph/table-lineage', methods=['POST'])
def get_table_lineage():
    try:
        data = request.get_json()
        table_name = data.get('table')
        
        with open(os.path.join('widget', 'json', 'lineageGraph.json'), 'r', encoding='utf-8') as f:
            graph_data = json.load(f)
        
        # 筛选与指定表相关的节点和边
        filtered_nodes = []
        filtered_edges = []
        
        if 'nodes' in graph_data:
            filtered_nodes = [node for node in graph_data['nodes'] 
                            if node.get('table') == table_name or 
                            (not node.get('name') and node.get('text') == table_name)]
        
        if filtered_nodes and 'edges' in graph_data:
            node_ids = set(node.get('id') for node in filtered_nodes)
            filtered_edges = [edge for edge in graph_data['edges']
                            if edge.get('source') in node_ids or edge.get('target') in node_ids]
        
        return jsonify({
            'nodes': filtered_nodes,
            'edges': filtered_edges
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/graph/column-lineage', methods=['POST'])
def get_column_lineage():
    try:
        data = request.get_json()
        table_name = data.get('table')
        column_name = data.get('column')
        
        with open(os.path.join('widget', 'json', 'lineageGraph.json'), 'r', encoding='utf-8') as f:
            graph_data = json.load(f)
        
        # 筛选与指定列相关的节点和边
        filtered_nodes = []
        filtered_edges = []
        
        if 'nodes' in graph_data:
            filtered_nodes = [node for node in graph_data['nodes']
                            if (node.get('table') == table_name and node.get('name') == column_name) or
                            (node.get('name') == column_name)]
        
        if filtered_nodes and 'edges' in graph_data:
            node_ids = set(node.get('id') for node in filtered_nodes)
            filtered_edges = [edge for edge in graph_data['edges']
                            if edge.get('source') in node_ids or edge.get('target') in node_ids]
        
        return jsonify({
            'nodes': filtered_nodes,
            'edges': filtered_edges
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/graph/table-columns', methods=['POST'])
def get_table_columns():
    try:
        data = request.get_json()
        table_name = data.get('table')
        
        with open(os.path.join('widget', 'json', 'lineageGraph.json'), 'r', encoding='utf-8') as f:
            graph_data = json.load(f)
        
        # 获取指定表的所有列
        columns = []
        if 'nodes' in graph_data:
            columns = [node for node in graph_data['nodes']
                      if node.get('table') == table_name and node.get('name')]
        
        return jsonify(columns)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # 确保json目录存在
    os.makedirs(os.path.join('widget', 'json'), exist_ok=True)
    
    # 初始化JVM
    init_jvm()
    
    # 注册退出时的清理函数
    atexit.register(cleanup_jvm)
    
    # 启动Flask应用
    app.run(host='0.0.0.0', port=8000)