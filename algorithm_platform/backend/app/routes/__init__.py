"""
# 算法迭代平台 - 路由注册
# 功能: 集中注册所有 Blueprint，统一 /api 前缀
"""


def register_routes(app):
    """注册所有路由 Blueprint"""
    from app.routes.health import health_bp
    app.register_blueprint(health_bp, url_prefix='/api')

    from app.routes.ingest import ingest_bp
    app.register_blueprint(ingest_bp, url_prefix='/api')

    from app.routes.events import events_bp
    app.register_blueprint(events_bp, url_prefix='/api')

    from app.routes.dashboard import dashboard_bp
    app.register_blueprint(dashboard_bp, url_prefix='/api')

    from app.routes.export import export_bp
    app.register_blueprint(export_bp, url_prefix='/api')

    from app.routes.seed import seed_bp
    app.register_blueprint(seed_bp, url_prefix='/api')

    return app
