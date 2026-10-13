# Ponto de entrada WSGI. Localmente: `flask run` já usa isso via FLASK_APP.
# No PythonAnywhere: o conteúdo do arquivo de configuração WSGI que o painel
# Web gera (ex: /var/www/seuusuario_pythonanywhere_com_wsgi.py) deve
# importar `application` DAQUI — ver README.md, seção "Deploy no
# PythonAnywhere", passo 4, pro trecho exato a colar lá.

from app import create_app

application = create_app()

if __name__ == "__main__":
    application.run(debug=True)
