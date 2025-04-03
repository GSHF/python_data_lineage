from flask import Flask, request, jsonify
import os
import tempfile
import json
import sys
import traceback
from io import StringIO
import contextlib
import jpype
import atexit
import threading

app = Flask(__name__, static_folder='widget')
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
    return app.send_static_file('index.html')

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
                    'snowflake': EDbVendor.dbvsnowflake
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

if __name__ == '__main__':
    # 确保json目录存在
    os.makedirs(os.path.join('widget', 'json'), exist_ok=True)
    
    # 初始化JVM
    init_jvm()
    
    # 注册退出时的清理函数
    atexit.register(cleanup_jvm)
    
    # 启动Flask应用
    app.run(port=8000, debug=False)
