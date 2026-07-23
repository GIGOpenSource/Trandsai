import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Card, Form, Input, Button, Select, InputNumber, Row, Col, Spin, Image, Space, Typography } from 'antd';
import { PictureOutlined, ReloadOutlined } from '@ant-design/icons';
import { adminFetchJson, showError, showSuccess } from '../../api/request';
import { resolveAdminMediaUrl } from '../../utils/mediaUrl';

const { TextArea } = Input;
const { Title } = Typography;

const MODELS = [
  { label: 'flux', value: 'flux' },
  { label: 'stable-diffusion', value: 'stable-diffusion' },
  { label: 'dreamshaper', value: 'dreamshaper' },
];

const STYLES = [
  { label: 'anime', value: 'anime' },
  { label: 'realistic', value: 'realistic' },
  { label: 'portrait', value: 'portrait' },
  { label: 'none', value: '' },
];

const SIZE_PRESETS = [
  { label: '512x512', value: '512x512' },
  { label: '768x768', value: '768x768' },
  { label: '1024x1024', value: '1024x1024' },
  { label: '1024x768', value: '1024x768' },
  { label: '768x1024', value: '768x1024' },
  { label: '1920x1080', value: '1920x1080' },
];

interface GenerateImageResponse {
  ok: boolean;
  image_url: string;
}

export default function ImageGenPage() {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [imageUrl, setImageUrl] = useState('');
  const [history, setHistory] = useState<string[]>([]);

  const handleSizeChange = (val: string) => {
    const [w, h] = val.split('x').map(Number);
    form.setFieldsValue({ width: w, height: h });
  };

  const handleSubmit = async (values: Record<string, unknown>) => {
    setLoading(true);
    try {
      const payload = {
        prompt: values.prompt,
        width: values.width,
        height: values.height,
        style: values.style,
        model: values.model,
        seed: values.seed || undefined,
        nologo: values.nologo !== false,
      };
      const res = await adminFetchJson<GenerateImageResponse>('/api/admin/generate-image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res && res.image_url) {
        setImageUrl(res.image_url);
        setHistory((prev) => [res.image_url, ...prev].slice(0, 8));
        showSuccess(t('toast.generated') as string);
      } else {
        showError(t('toast.generateFailed') as string);
      }
    } catch (e) {
      showError((e as Error).message || (t('toast.generateFailed') as string));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <Title level={4}>{t('page.imageGen')}</Title>
      <Row gutter={24}>
        <Col xs={24} lg={10}>
          <Card>
            <Form
              form={form}
              layout="vertical"
              onFinish={handleSubmit}
              initialValues={{
                prompt: '',
                width: 1024,
                height: 1024,
                style: 'anime',
                model: 'flux',
                nologo: true,
              }}
            >
              <Form.Item
                label={t('imageGen.prompt')}
                name="prompt"
                rules={[{ required: true, message: t('imageGen.promptRequired') as string }]}
              >
                <TextArea rows={4} placeholder={t('imageGen.promptPlaceholder') as string} />
              </Form.Item>

              <Form.Item label={t('imageGen.sizePreset')}>
                <Select options={SIZE_PRESETS} onChange={handleSizeChange} placeholder={t('imageGen.sizePreset') as string} allowClear />
              </Form.Item>

              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item label={t('imageGen.width')} name="width">
                    <InputNumber min={64} max={2048} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label={t('imageGen.height')} name="height">
                    <InputNumber min={64} max={2048} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>

              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item label={t('imageGen.style')} name="style">
                    <Select options={STYLES} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label={t('imageGen.model')} name="model">
                    <Select options={MODELS} allowClear />
                  </Form.Item>
                </Col>
              </Row>

              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item label={t('imageGen.seed')} name="seed">
                    <InputNumber min={0} style={{ width: '100%' }} placeholder={t('imageGen.seedPlaceholder') as string} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label={t('imageGen.nologo')} name="nologo" valuePropName="checked">
                    <Select
                      options={[
                        { label: t('yes'), value: true },
                        { label: t('no'), value: false },
                      ]}
                    />
                  </Form.Item>
                </Col>
              </Row>

              <Form.Item>
                <Button type="primary" htmlType="submit" icon={<PictureOutlined />} loading={loading} block>
                  {t('btn.generate')}
                </Button>
              </Form.Item>
            </Form>
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card title={t('imageGen.preview')}>
            {loading ? (
              <div style={{ textAlign: 'center', padding: 80 }}>
                <Spin size="large" />
                <div style={{ marginTop: 16 }}>{t('imageGen.generating')}</div>
              </div>
            ) : imageUrl ? (
              <div style={{ textAlign: 'center' }}>
                <Image
                  src={resolveAdminMediaUrl(imageUrl)}
                  alt="generated"
                  style={{ maxWidth: '100%', maxHeight: 600, borderRadius: 8 }}
                  placeholder={<Spin />}
                  fallback="https://via.placeholder.com/400x300?text=图片加载失败"
                />
                <div style={{ marginTop: 12 }}>
                  <Space>
                    <Button icon={<ReloadOutlined />} onClick={() => form.submit()}>
                      {t('btn.regenerate')}
                    </Button>
                    <Button href={resolveAdminMediaUrl(imageUrl)} target="_blank" rel="noreferrer">
                      {t('btn.open')}
                    </Button>
                  </Space>
                </div>
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: 80, color: '#999' }}>
                <PictureOutlined style={{ fontSize: 48 }} />
                <div style={{ marginTop: 16 }}>{t('imageGen.empty')}</div>
              </div>
            )}
          </Card>

          {history.length > 1 && (
            <Card title={t('imageGen.history')} style={{ marginTop: 16 }}>
              <Row gutter={[8, 8]}>
                {history.slice(1).map((url, idx) => (
                  <Col key={idx} span={6}>
                    <Image
                      src={resolveAdminMediaUrl(url)}
                      style={{ width: '100%', height: 120, objectFit: 'cover', borderRadius: 4, cursor: 'pointer' }}
                      preview={false}
                      onClick={() => setImageUrl(url)}
                      fallback="https://via.placeholder.com/120x120?text=加载失败"
                    />
                  </Col>
                ))}
              </Row>
            </Card>
          )}
        </Col>
      </Row>
    </div>
  );
}
