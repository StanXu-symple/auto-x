from alembic import op
import sqlalchemy as sa
revision='0014_qq_task_interval'; down_revision='0013_qq_delivery_task'; branch_labels=None; depends_on=None
def upgrade(): op.add_column('qq_scheduled_tasks',sa.Column('interval_value',sa.Integer(),nullable=False,server_default='1'))
def downgrade(): op.drop_column('qq_scheduled_tasks','interval_value')
